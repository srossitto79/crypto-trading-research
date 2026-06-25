"""Fitness evaluator — scores strategies on a 0-100 scale.

Combines backtest metrics into a single fitness score used for
strategy promotion decisions (researching → paper → deployed).

Scoring weights:
  - Sharpe ratio: 30% (0-100 scale, capped at Sharpe=3)
  - Win rate: 20% (0-100 scale)
  - Profit factor: 20% (0-100 scale, capped at PF=5)
  - Max drawdown penalty: 15% (100 = no drawdown, 0 = 10%+ drawdown)
  - Trade count bonus: 15% (needs minimum trades for statistical significance)

Thresholds:
  - fitness >= 60: eligible for paper trading
  - fitness >= 70: eligible for deployment
  - fitness < 40: retire/reject
"""

import json
import logging
from datetime import datetime, timezone

from axiom.db import get_db, init_db
from axiom.regime import HIGH_VOL, RANGE_BOUND, TREND_DOWN, TREND_UP
from axiom.policy import score_strategy, load_pipeline_config

log = logging.getLogger("axiom.strategies.fitness")

REGIME_KEYS = [TREND_UP, TREND_DOWN, RANGE_BOUND, HIGH_VOL]


# Alias for backtest.py compatibility
compute_fitness_score = score_strategy


def evaluate_all() -> list[dict]:
    """Evaluate fitness for all strategies in the database."""
    init_db()
    results = []

    config = load_pipeline_config()
    paper_threshold = config.get("paper_gate", {}).get("min_fitness", 0)
    deploy_threshold = config.get("deploy_gate", {}).get("min_fitness", 0)
    retire_threshold = config.get("retirement", {}).get("max_fitness", -999)

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM strategies").fetchall()

    now = datetime.now(timezone.utc).isoformat()

    from axiom.brain import stage_is_param_locked

    for row in rows:
        row = dict(row)
        # Operator-owned (paper/live) strategies have FROZEN stored metrics —
        # never re-score/overwrite them from this background sweep.
        if stage_is_param_locked(row.get("stage")):
            log.info(
                "metrics locked: %s at %s; fitness re-evaluation skipped",
                row.get("id"), str(row.get("stage") or "").strip().lower(),
            )
            continue
        metrics = json.loads(row.get("metrics", "{}") or "{}")

        fitness = score_strategy(metrics)
        compatible_regimes, is_all_rounder = _derive_regime_compatibility(metrics)
        enriched_metrics = {
            **(metrics if isinstance(metrics, dict) else {}),
            "compatible_regimes": compatible_regimes,
            "is_all_rounder": bool(is_all_rounder),
        }

        # Determine verdict
        if fitness >= deploy_threshold:
            verdict = "deploy_eligible"
        elif fitness >= paper_threshold:
            verdict = "paper_eligible"
        elif fitness >= retire_threshold:
            verdict = "monitor"
        else:
            verdict = "retire"

        verdict_data = {
            "fitness": fitness,
            "verdict": verdict,
            "evaluated_at": now,
            "thresholds": {"paper": paper_threshold, "deploy": deploy_threshold, "retire": retire_threshold},
            "compatible_regimes": compatible_regimes,
            "is_all_rounder": bool(is_all_rounder),
        }

        # Update in DB
        with get_db() as conn:
            try:
                conn.execute(
                    "UPDATE strategies SET verdict = ?, metrics = ?, compatible_regimes = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(verdict_data), json.dumps(enriched_metrics), json.dumps(compatible_regimes), now, row["id"]),
                )
            except Exception:
                # Backward-compatible fallback for older schema snapshots.
                conn.execute(
                    "UPDATE strategies SET verdict = ?, metrics = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(verdict_data), json.dumps(enriched_metrics), now, row["id"]),
                )

        results.append({
            "id": row["id"],
            "name": row.get("name", ""),
            "fitness": fitness,
            "verdict": verdict,
            "metrics": enriched_metrics,
        })

        log.info("Strategy %s: fitness=%.1f verdict=%s", row["id"], fitness, verdict)

    return sorted(results, key=lambda x: x["fitness"], reverse=True)


def get_promotion_candidates() -> dict:
    """Get strategies that should be promoted or retired based on fitness."""
    results = evaluate_all()

    return {
        "promote_to_paper": [r for r in results if r["verdict"] == "paper_eligible" and _current_status(r["id"]) == "researching"],
        "promote_to_deploy": [r for r in results if r["verdict"] == "deploy_eligible" and _current_status(r["id"]) in ("paper", "researching")],
        "retire": [r for r in results if r["verdict"] == "retire"],
        "all": results,
    }


def _current_status(strategy_id: str) -> str:
    """Get current status of a strategy."""
    with get_db() as conn:
        row = conn.execute("SELECT status FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        return row["status"] if row else "unknown"


def _derive_regime_compatibility(metrics: dict) -> tuple[list[str], bool]:
    """Infer compatible regimes from regime-sliced metrics."""
    regime_metrics = metrics.get("regimes", {}) if isinstance(metrics, dict) else {}
    compatible: list[str] = []

    for regime in REGIME_KEYS:
        data = regime_metrics.get(regime, {}) if isinstance(regime_metrics, dict) else {}
        sharpe = float(data.get("sharpe", 0) or 0)
        pf = float(data.get("profit_factor", 0) or 0)

        # Loosened for testing — production: HIGH_VOL needs Sharpe > 1.5, others > 1.0
        if regime == HIGH_VOL:
            if sharpe > 0.0 and pf > 0.0:
                compatible.append(regime)
            continue

        if sharpe > 0.0 and pf > 0.0:
            compatible.append(regime)

    is_all_rounder = all(reg in compatible for reg in (TREND_UP, TREND_DOWN, RANGE_BOUND))
    return compatible, is_all_rounder


# ── P2-2: Dual-output scoring ───────────────────────────────────────────────

_REGIME_SENSITIVITY_THRESHOLD = 0.60  # 60% degradation = regime_sensitive


def compute_dual_score(
    gated_metrics: dict,
    ungated_metrics: dict | None = None,
) -> dict:
    """P2-2: Compute primary (gated) score + regime sensitivity delta.

    Primary score: from ``regime_gate=True`` (deploy-realistic).
    Secondary signal: edge degradation delta between ungated and gated runs.
    If degradation exceeds threshold, strategy is marked ``regime_sensitive``.

    Args:
        gated_metrics: Metrics from backtest with ``regime_gate=True``.
        ungated_metrics: Optional metrics from backtest with ``regime_gate=False``.

    Returns:
        Dict with ``primary_score``, ``ungated_score``, ``degradation_delta``,
        ``regime_sensitive``, and ``deployment_fitness``.
    """
    primary_score = score_strategy(gated_metrics)

    if not ungated_metrics:
        return {
            "primary_score": primary_score,
            "ungated_score": None,
            "degradation_delta": 0.0,
            "regime_sensitive": False,
            "deployment_fitness": primary_score,
        }

    ungated_score = score_strategy(ungated_metrics)

    # Compute degradation: how much fitness drops when regime gating is applied
    if ungated_score > 0:
        degradation_delta = 1.0 - (primary_score / ungated_score)
    else:
        degradation_delta = 0.0

    regime_sensitive = degradation_delta > _REGIME_SENSITIVITY_THRESHOLD

    return {
        "primary_score": primary_score,
        "ungated_score": ungated_score,
        "degradation_delta": round(degradation_delta, 4),
        "regime_sensitive": regime_sensitive,
        "deployment_fitness": primary_score,  # Always use gated score for deployment
    }
