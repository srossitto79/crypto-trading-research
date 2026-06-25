"""Phase 5: lineage-aware iteration helpers.

Builds two structured artifacts that the strategy-developer agent uses when
proposing the next strategy under a hypothesis:

  1. Sibling table — every prior child of the hypothesis (id, params, asset,
     timeframe, regime filter, backtest metrics, status, parent_strategy_id).
     The agent uses this to either mutate the best sibling or fill an
     uncovered cell.

  2. Canonical-coverage map — for revisited hypotheses, a `(asset, timeframe)
     → canonical_strategy` map showing which cells already have a frozen
     winner. The agent should produce variants that beat or don't conflict
     with these.
"""
from __future__ import annotations

import json
from typing import Any

from axiom.db import get_db


def build_sibling_table(hypothesis_id: str) -> list[dict[str, Any]]:
    """Return one row per active sibling strategy of the hypothesis.

    Excludes archived / rejected strategies so the agent doesn't spend tokens
    reasoning about dead variants.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT s.id,
                   s.display_id,
                   s.name,
                   s.type,
                   s.symbol,
                   s.timeframe,
                   s.stage,
                   s.status,
                   s.params,
                   s.parent_strategy_id,
                   s.canonical,
                   s.created_at,
                   (SELECT metrics_json FROM backtest_results r
                    WHERE r.strategy_id = s.id AND r.deleted_at IS NULL
                    ORDER BY r.created_at DESC LIMIT 1) AS latest_metrics
            FROM strategies s
            WHERE s.hypothesis_id = ?
              AND s.stage NOT IN ('archived', 'rejected')
            ORDER BY s.created_at ASC
            """,
            (hypothesis_id,),
        ).fetchall()

    siblings: list[dict[str, Any]] = []
    for row in rows:
        params = _safe_json(row["params"])
        metrics = _safe_json(row["latest_metrics"])
        regime_filter = None
        if isinstance(params, dict):
            regime_filter = params.get("regime_filter") or params.get("regime")
        siblings.append({
            "strategy_id": row["id"],
            "display_id": row["display_id"],
            "name": row["name"],
            "type": row["type"],
            "asset": row["symbol"],
            "timeframe": row["timeframe"],
            "stage": row["stage"],
            "status": row["status"],
            "regime_filter": regime_filter,
            "params": params if isinstance(params, dict) else {},
            "parent_strategy_id": row["parent_strategy_id"],
            "canonical": bool(row["canonical"]),
            "backtest_metrics": _summarize_metrics(metrics),
            "created_at": row["created_at"],
        })
    return siblings


def build_canonical_coverage_map(hypothesis_id: str) -> dict[str, dict[str, Any]]:
    """Return `(asset, timeframe)` → best canonical strategy for the hypothesis.

    Map keys are formatted `"ASSET:TIMEFRAME"` (string-safe for JSON). Empty
    map for hypotheses with no canonicals (i.e. not yet graduated).
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT s.id,
                   s.display_id,
                   s.symbol,
                   s.timeframe,
                   s.stage,
                   (SELECT metrics_json FROM backtest_results r
                    WHERE r.strategy_id = s.id AND r.deleted_at IS NULL
                    ORDER BY r.created_at DESC LIMIT 1) AS latest_metrics
            FROM strategies s
            WHERE s.hypothesis_id = ?
              AND s.canonical = 1
              AND s.stage NOT IN ('archived', 'rejected')
            """,
            (hypothesis_id,),
        ).fetchall()

    coverage: dict[str, dict[str, Any]] = {}
    for row in rows:
        cell_key = f"{row['symbol']}:{row['timeframe']}"
        coverage[cell_key] = {
            "strategy_id": row["id"],
            "display_id": row["display_id"],
            "asset": row["symbol"],
            "timeframe": row["timeframe"],
            "stage": row["stage"],
            "backtest_metrics": _summarize_metrics(_safe_json(row["latest_metrics"])),
        }
    return coverage


def _safe_json(blob: Any) -> Any:
    if blob is None:
        return None
    if isinstance(blob, (dict, list)):
        return blob
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return None


def _summarize_metrics(metrics: Any) -> dict[str, Any]:
    """Pull the small subset of fields the agent actually needs."""
    if not isinstance(metrics, dict):
        return {}
    return {
        "sharpe": metrics.get("sharpe_ratio") or metrics.get("sharpe"),
        "total_return_pct": metrics.get("total_return_pct") or metrics.get("total_return"),
        "total_trades": metrics.get("total_trades") or metrics.get("num_trades"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct") or metrics.get("max_drawdown"),
        "win_rate": metrics.get("win_rate"),
    }
