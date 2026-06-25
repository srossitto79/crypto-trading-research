"""Phase 4 matrix engine: queue-only regime backtest matrix, admission, and champions."""

from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from axiom.data import load_parquet
from axiom.lab_db import (
    LabJobState,
    append_strategy_regime_observations,
    claim_next_lab_job,
    get_blacklisted_strategy_ids,
    get_lab_experiment,
    get_lab_job,
    get_model_version,
    get_previous_regime_container_snapshot,
    get_regime_container_snapshot,
    get_regime_segments,
    get_snapshot_manifest,
    heartbeat_lab_job,
    list_latest_strategy_regime_observations,
    list_regime_container_snapshots,
    record_strategy_timeout,
    replace_regime_containers,
    replace_strategy_regime_scores,
    set_lab_job_state,
    update_lab_experiment_status,
)
from axiom.lab_regime_engine import (
    REGIME_TAXONOMY,
    TRANSITION_OVERLAY,
    experiment_execution_timeframe,
    experiment_regime_timeframe,
    normalize_core_regime,
    regime_query_aliases,
)
from axiom.lab_strategy_pool import (
    LAB_STRATEGY_SOURCE_REGISTRY,
    list_strategy_pool_candidates,
    normalize_strategy_sources,
)
from axiom.strategies.backtest import backtest_strategy, expand_strategy_trade_modes, validate_backtest_risk_controls
from axiom.strategies.base import BaseStrategy
from axiom.strategies.registry import discover, get_all

MATRIX_JOB_TYPE = "backtests_matrix"
DEFAULT_MIN_MATRIX_WINDOW_BARS = 210
MIN_REGIME_TRADE_COUNT = 75
MIN_OOS_TRADE_COUNT = 30
RELAXED_MIN_REGIME_TRADE_COUNT = 10
RELAXED_MIN_OOS_TRADE_COUNT = 5
BORDERLINE_MIN_TOTAL_RETURN_PCT = -0.05
BORDERLINE_MIN_PROFIT_FACTOR = 0.95
BORDERLINE_MIN_SHARPE = -0.05
DEFAULT_RESERVE_COUNT = 3
DEFAULT_MIN_CHAMPION_DWELL_HOURS = 24
DEFAULT_MIN_CHAMPION_SCORE_DELTA = 0.08
DEFAULT_GRAVEYARD_REQUIRED_WINS = 2

BASE_FEE_BPS = 4.5
BASE_SLIPPAGE_BPS = 2.0
BASE_EXECUTION_COST_BPS = BASE_FEE_BPS + BASE_SLIPPAGE_BPS
MAX_NON_VECTORIZED_MATRIX_BARS = 10_000

# STRATEGY-LOSS fix (lab-matrix-retry): a single transient worker error
# (timeout / IO / connection / pool death) used to mark a strategy
# admitted:False permanently with no retry — in an adverse regime that can
# silently drop the only viable challenger. Retry transient errors in-process
# a bounded number of times before accepting the not-admitted result.
MATRIX_CELL_MAX_RETRIES = 2  # => up to 3 total attempts per cell

EXECUTION_PENALTY_MULTIPLIERS: dict[str, float] = {
    "HIGH_VOL": 2.10,
    TRANSITION_OVERLAY: 2.30,
}

TIMEFRAME_MIN_MATRIX_WINDOW_BARS: dict[str, int] = {
    "5m": 144,
    "15m": 96,
    "30m": 120,
    "1h": 168,
}

DEFAULT_WATCHDOG_STALL_TIMEOUT_SECONDS = 300


class WatchdogTimeoutError(Exception):
    """Raised when a matrix job stalls with no progress."""
    pass


log = logging.getLogger("axiom.lab_matrix_engine")

SAMPLE_COUNT_GATE_KEYS = frozenset({"trades_gte_75", "oos_trades_gte_30"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def resolve_min_matrix_window_bars(timeframe: str | None) -> int:
    normalized = str(timeframe or "").strip().lower()
    if normalized in TIMEFRAME_MIN_MATRIX_WINDOW_BARS:
        return int(TIMEFRAME_MIN_MATRIX_WINDOW_BARS[normalized])
    return int(DEFAULT_MIN_MATRIX_WINDOW_BARS)


def _normalize_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    data = frame.copy()
    for column in required:
        if column not in data.columns:
            raise ValueError(f"Snapshot frame missing required column '{column}'")
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=required)
    data = data.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    data = data.set_index("timestamp", drop=False)
    return data


def _metrics_from_trade_returns(returns: list[float], *, stability: float = 0.0) -> dict[str, float]:
    if not returns:
        return {
            "total_trades": 0.0,
            "total_return_pct": 0.0,
            "profit_factor": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate": 0.0,
            "avg_trade_pct": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "stability": float(stability),
        }

    cleaned = [max(-0.99, float(value)) for value in returns]
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for trade_return in cleaned:
        equity *= 1.0 + trade_return
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / max(peak, 1e-9)
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    gross_profit = float(sum(value for value in cleaned if value > 0))
    gross_loss = float(sum(value for value in cleaned if value < 0))
    pf = gross_profit / abs(gross_loss) if gross_loss < 0 else (999.0 if gross_profit > 0 else 0.0)
    arr = np.array(cleaned, dtype=float)
    mean_return = float(arr.mean())
    std_return = float(arr.std(ddof=0))
    sharpe = (mean_return / std_return) * math.sqrt(len(cleaned)) if std_return > 0 else (3.0 if mean_return > 0 else 0.0)

    return {
        "total_trades": float(len(cleaned)),
        "total_return_pct": float(equity - 1.0),
        "profit_factor": float(pf),
        "sharpe": float(sharpe),
        "max_drawdown_pct": float(max_drawdown),
        "win_rate": float((arr > 0).sum() / len(cleaned)),
        "avg_trade_pct": float(mean_return),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "stability": float(stability),
    }


def get_execution_penalty_multiplier(regime: str) -> float:
    normalized_regime = normalize_core_regime(regime)
    if normalized_regime is not None:
        return float(EXECUTION_PENALTY_MULTIPLIERS.get(normalized_regime, 1.0))
    return float(EXECUTION_PENALTY_MULTIPLIERS.get(str(regime).upper(), 1.0))


def _get_previous_snapshot_compatible(
    *,
    experiment_id: str,
    regime: str,
    exclude_model_version_id: str | None = None,
) -> dict[str, Any] | None:
    for alias in regime_query_aliases(regime):
        snapshot = get_previous_regime_container_snapshot(
            experiment_id=experiment_id,
            regime=alias,
            exclude_model_version_id=exclude_model_version_id,
        )
        if snapshot is not None:
            return snapshot
    return None


def _get_current_snapshot_compatible(*, model_version_id: str, regime: str) -> dict[str, Any] | None:
    for alias in regime_query_aliases(regime):
        snapshot = get_regime_container_snapshot(model_version_id=model_version_id, regime=alias)
        if snapshot is not None:
            return snapshot
    return None


def apply_regime_execution_penalty(
    *,
    regime: str,
    raw_metrics: dict[str, float],
    trade_returns: list[float],
    base_execution_cost_bps: float = BASE_EXECUTION_COST_BPS,
) -> dict[str, float]:
    multiplier = get_execution_penalty_multiplier(regime)
    incremental_drag = max(0.0, (multiplier - 1.0) * float(base_execution_cost_bps)) / 10_000.0
    penalized_returns = [float(value) - incremental_drag for value in trade_returns]
    adjusted = _metrics_from_trade_returns(
        penalized_returns,
        stability=float(raw_metrics.get("stability") or 0.0),
    )
    adjusted["execution_penalty_multiplier"] = float(multiplier)
    adjusted["execution_drag_per_trade"] = float(incremental_drag)
    return adjusted


def evaluate_admission_gates(metrics: dict[str, float]) -> dict[str, Any]:
    checks = {
        "trades_gte_75": float(metrics.get("total_trades") or 0.0) >= float(MIN_REGIME_TRADE_COUNT),
        "oos_trades_gte_30": float(metrics.get("oos_forward_total_trades") or 0.0) >= float(MIN_OOS_TRADE_COUNT),
        "post_cost_return_positive": float(metrics.get("total_return_pct") or 0.0) > 0.0,
        "profit_factor_gte_1_10": float(metrics.get("profit_factor") or 0.0) >= 1.10,
        "sharpe_gte_0_30": float(metrics.get("sharpe") or 0.0) >= 0.30,
        "max_drawdown_lte_0_35": float(metrics.get("max_drawdown_pct") or 0.0) <= 0.35,
        "oos_forward_return_non_negative": float(metrics.get("oos_forward_total_return_pct") or 0.0) >= 0.0,
        "oos_forward_pf_gte_1_0": float(metrics.get("oos_forward_profit_factor") or 0.0) >= 1.0,
    }
    relaxed_checks = {
        "trades_gte_10": float(metrics.get("total_trades") or 0.0) >= float(RELAXED_MIN_REGIME_TRADE_COUNT),
        "oos_trades_gte_5": float(metrics.get("oos_forward_total_trades") or 0.0) >= float(RELAXED_MIN_OOS_TRADE_COUNT),
        "strict_non_sample_pass": all(
            bool(passed) for key, passed in checks.items() if key not in SAMPLE_COUNT_GATE_KEYS
        ),
    }
    borderline_checks = {
        "trades_gte_10": float(metrics.get("total_trades") or 0.0) >= float(RELAXED_MIN_REGIME_TRADE_COUNT),
        "oos_trades_gte_5": float(metrics.get("oos_forward_total_trades") or 0.0) >= float(RELAXED_MIN_OOS_TRADE_COUNT),
        "post_cost_return_gte_neg_0_05": float(metrics.get("total_return_pct") or 0.0)
        >= float(BORDERLINE_MIN_TOTAL_RETURN_PCT),
        "profit_factor_gte_0_95": float(metrics.get("profit_factor") or 0.0) >= float(BORDERLINE_MIN_PROFIT_FACTOR),
        "sharpe_gte_neg_0_05": float(metrics.get("sharpe") or 0.0) >= float(BORDERLINE_MIN_SHARPE),
        "max_drawdown_lte_0_35": float(metrics.get("max_drawdown_pct") or 0.0) <= 0.35,
        "oos_forward_return_non_negative": float(metrics.get("oos_forward_total_return_pct") or 0.0) >= 0.0,
        "oos_forward_pf_gte_1_0": float(metrics.get("oos_forward_profit_factor") or 0.0) >= 1.0,
    }
    return {
        "admitted": all(checks.values()),
        "checks": checks,
        "fallback_eligible": all(relaxed_checks.values()),
        "relaxed_checks": relaxed_checks,
        "borderline_eligible": all(borderline_checks.values()),
        "borderline_checks": borderline_checks,
    }


def _minmax_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high <= low:
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def _score_admitted_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    sharpe_values = [float(row["adjusted_metrics"]["sharpe"]) for row in candidates]
    pf_values = [float(row["adjusted_metrics"]["profit_factor"]) for row in candidates]
    return_values = [float(row["adjusted_metrics"]["total_return_pct"]) for row in candidates]
    dd_values = [float(row["adjusted_metrics"]["max_drawdown_pct"]) for row in candidates]
    stability_values = [float(row["adjusted_metrics"].get("stability") or 0.0) for row in candidates]
    trade_density_values = [
        float(
            min(
                row["adjusted_metrics"].get("trade_density_per_100_bars") or 0.0,
                row["adjusted_metrics"].get("oos_trade_density_per_100_bars") or 0.0,
            )
        )
        for row in candidates
    ]

    sharpe_norm = _minmax_normalize(sharpe_values)
    pf_norm = _minmax_normalize(pf_values)
    return_norm = _minmax_normalize(return_values)
    dd_norm = _minmax_normalize(dd_values)
    stability_norm = _minmax_normalize(stability_values)
    trade_density_norm = _minmax_normalize(trade_density_values)

    scored: list[dict[str, Any]] = []
    for idx, row in enumerate(candidates):
        dd_inverse = 1.0 - dd_norm[idx]
        score = (
            0.28 * sharpe_norm[idx]
            + 0.24 * pf_norm[idx]
            + 0.15 * return_norm[idx]
            + 0.13 * dd_inverse
            + 0.10 * stability_norm[idx]
            + 0.10 * trade_density_norm[idx]
        )
        enriched = dict(row)
        enriched["score_components"] = {
            "sharpe_norm": sharpe_norm[idx],
            "pf_norm": pf_norm[idx],
            "return_norm": return_norm[idx],
            "dd_inverse_norm": dd_inverse,
            "stability_norm": stability_norm[idx],
            "trade_density_norm": trade_density_norm[idx],
        }
        enriched["score"] = float(score)
        scored.append(enriched)

    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    for rank, item in enumerate(scored, start=1):
        item["rank"] = rank
    return scored


def _ranked_selection_pool(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    strict_candidates = [
        candidate
        for candidate in candidates
        if bool(dict(candidate.get("admission") or {}).get("admitted"))
    ]
    if strict_candidates:
        return strict_candidates, "strict"

    fallback_candidates = [
        candidate
        for candidate in candidates
        if bool(dict(candidate.get("admission") or {}).get("fallback_eligible"))
    ]
    if fallback_candidates:
        return fallback_candidates, "fallback_sampling_shortfall"

    borderline_candidates = [
        candidate
        for candidate in candidates
        if bool(dict(candidate.get("admission") or {}).get("borderline_eligible"))
    ]
    if borderline_candidates:
        return borderline_candidates, "fallback_borderline_validated"
    return [], "none"


def _trade_density(total_trades: float, bars_count: int) -> float:
    if bars_count <= 0:
        return 0.0
    return float(total_trades) / float(bars_count) * 100.0


def _parse_iso_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _row_strategy_meta(row: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(row or {})
    metrics = dict(payload.get("metrics_json") or {})
    strategy_meta = dict(metrics.get("strategy_meta") or {})
    rationale = dict(payload.get("rationale_json") or {})
    if rationale:
        for key in ("strategy_id", "candidate_key", "trade_mode", "position_model", "strategy_name"):
            value = rationale.get(key)
            if value is not None and strategy_meta.get(key) in (None, ""):
                strategy_meta[key] = value
    return strategy_meta


def _strategy_identity(row: dict[str, Any] | None) -> str:
    payload = dict(row or {})
    strategy_meta = _row_strategy_meta(payload)
    return (
        str(payload.get("candidate_key") or strategy_meta.get("candidate_key") or payload.get("strategy_id") or "").strip()
        or str(payload.get("strategy_id") or "").strip()
    )


def _base_strategy_id(row: dict[str, Any] | None) -> str:
    payload = dict(row or {})
    strategy_meta = _row_strategy_meta(payload)
    return (
        str(strategy_meta.get("strategy_id") or payload.get("strategy_id") or "").strip()
        or str(payload.get("strategy_id") or "").strip()
    )


def _row_trade_mode(row: dict[str, Any] | None) -> str:
    payload = dict(row or {})
    strategy_meta = _row_strategy_meta(payload)
    return str(payload.get("trade_mode") or strategy_meta.get("trade_mode") or "long_only").strip() or "long_only"


def _previous_selection_meta(previous_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(previous_snapshot or {})
    meta_json = dict(snapshot.get("meta_json") or {})
    champion_selection = dict(meta_json.get("champion_selection") or {})
    selection_evidence = dict(snapshot.get("selection_evidence") or {})
    merged = dict(meta_json)
    merged.update(champion_selection)
    merged.update(selection_evidence)
    return merged


def _select_champion_with_guardrails(
    *,
    regime: str,
    scored: list[dict[str, Any]],
    previous_snapshot: dict[str, Any] | None,
    reserve_count: int,
    min_champion_dwell_hours: int,
    min_champion_score_delta: float,
    graveyard_required_wins: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    current_time = datetime.now(UTC)
    previous_meta = dict((previous_snapshot or {}).get("meta_json") or {})
    previous_selection_meta = _previous_selection_meta(previous_snapshot)
    previous_champion = dict((previous_snapshot or {}).get("champion") or {})
    previous_champion_id = _strategy_identity(previous_champion) or None
    previous_selected_at = _parse_iso_datetime(
        previous_selection_meta.get("champion_selected_at")
        or previous_meta.get("champion_selected_at")
        or previous_champion.get("created_at")
    )
    scored_by_id = {_strategy_identity(row): row for row in scored}
    proposed = scored[0] if scored else None
    selected = proposed
    selection_reason = "new_best_score" if proposed else "no_admitted_candidates"
    score_delta = None
    dwell_hours = None
    pending_graveyard_candidate_id = None
    pending_graveyard_wins = 0

    if previous_champion_id and previous_champion_id in scored_by_id and previous_selected_at is not None:
        dwell_hours = max(0.0, (current_time - previous_selected_at).total_seconds() / 3600.0)

    if proposed and previous_champion_id and previous_champion_id in scored_by_id:
        previous_current = scored_by_id[previous_champion_id]
        score_delta = float(proposed["score"]) - float(previous_current["score"])
        if _strategy_identity(proposed) == previous_champion_id:
            selected = proposed
            selection_reason = "champion_defended"
        else:
            if dwell_hours is not None and dwell_hours < float(min_champion_dwell_hours) and float(score_delta) < float(min_champion_score_delta):
                selected = previous_current
                selection_reason = "held_min_dwell"
            elif float(score_delta) < float(min_champion_score_delta):
                selected = previous_current
                selection_reason = "held_small_delta"

    if proposed and _strategy_identity(proposed) != previous_champion_id:
        proposed_source = str(proposed.get("strategy_source") or "").strip().lower()
        if proposed_source == "graveyard":
            prior_pending_id = str(previous_selection_meta.get("pending_graveyard_candidate_id") or "").strip()
            prior_pending_wins = int(previous_selection_meta.get("pending_graveyard_wins") or 0)
            pending_graveyard_candidate_id = _strategy_identity(proposed)
            pending_graveyard_wins = prior_pending_wins + 1 if prior_pending_id == pending_graveyard_candidate_id else 1
            if pending_graveyard_wins < max(1, int(graveyard_required_wins)):
                if previous_champion_id and previous_champion_id in scored_by_id:
                    selected = scored_by_id[previous_champion_id]
                    selection_reason = "graveyard_pending_validation"
                else:
                    selected = None
                    selection_reason = "graveyard_pending_validation"
            elif selected is proposed:
                selection_reason = "graveyard_promoted"

    if selected and previous_champion_id and _strategy_identity(selected) == previous_champion_id and previous_selected_at is not None:
        champion_selected_at = previous_selected_at.isoformat()
    else:
        champion_selected_at = current_time.isoformat() if selected else None

    selected_strategy_id = _strategy_identity(selected) if selected else None
    reserve_rows = [row for row in scored if _strategy_identity(row) != selected_strategy_id]
    reserves = [
        {
            "strategy_id": _strategy_identity(row),
            "base_strategy_id": _base_strategy_id(row),
            "candidate_key": _strategy_identity(row),
            "trade_mode": _row_trade_mode(row),
            "position_model": str(row.get("position_model") or _row_strategy_meta(row).get("position_model") or "single_side"),
            "rank": index + 1,
            "score": float(row["score"]),
            "source_pool": str(row.get("strategy_source") or ""),
            "source_stage": row.get("strategy_stage"),
            "strategy_name": row.get("strategy_name"),
            "score_components": dict(row.get("score_components") or {}),
        }
        for index, row in enumerate(reserve_rows[: max(1, int(reserve_count))])
    ]

    graveyard_resurrections = [
        {
            "strategy_id": _strategy_identity(row),
            "base_strategy_id": _base_strategy_id(row),
            "trade_mode": _row_trade_mode(row),
            "score": float(row["score"]),
            "status": ("active_champion" if selected_strategy_id == _strategy_identity(row) else "reserve"),
            "strategy_name": row.get("strategy_name"),
        }
        for row in scored
        if str(row.get("strategy_source") or "").strip().lower() == "graveyard"
    ]

    selection_meta = {
        "proposed_strategy_id": (_strategy_identity(proposed) if proposed else None),
        "selected_strategy_id": selected_strategy_id,
        "previous_champion_strategy_id": previous_champion_id,
        "selection_reason": selection_reason,
        "score_delta": score_delta,
        "current_dwell_hours": dwell_hours,
        "min_dwell_hours": int(min_champion_dwell_hours),
        "min_score_delta": float(min_champion_score_delta),
        "graveyard_required_wins": int(graveyard_required_wins),
        "pending_graveyard_candidate_id": pending_graveyard_candidate_id,
        "pending_graveyard_wins": pending_graveyard_wins,
        "champion_selected_at": champion_selected_at,
        "reserves": reserves,
        "graveyard_resurrections": graveyard_resurrections,
    }

    champion_payload = (
        {
            "strategy_id": selected_strategy_id,
            "score": float(selected["score"]),
            "rationale_json": {
                "rank": int(selected.get("rank") or 1),
                "strategy_id": _base_strategy_id(selected),
                "candidate_key": _strategy_identity(selected),
                "trade_mode": _row_trade_mode(selected),
                "position_model": str(selected.get("position_model") or _row_strategy_meta(selected).get("position_model") or "single_side"),
                "strategy_source": selected.get("strategy_source"),
                "strategy_stage": selected.get("strategy_stage"),
                "strategy_name": selected.get("strategy_name"),
                "score_components": selected.get("score_components") or {},
                "admission_checks": dict(selected.get("admission", {}).get("checks") or {}),
                "oos_adjusted": dict(selected.get("oos_adjusted_metrics") or {}),
                "selection_reason": selection_reason,
                "previous_champion_strategy_id": previous_champion_id,
                "score_delta": score_delta,
                "current_dwell_hours": dwell_hours,
                "guardrails": {
                    "min_dwell_hours": int(min_champion_dwell_hours),
                    "min_score_delta": float(min_champion_score_delta),
                    "graveyard_required_wins": int(graveyard_required_wins),
                },
            },
        }
        if selected is not None
        else None
    )
    return champion_payload, reserves, selection_meta


@dataclass
class StrategyCandidate:
    strategy_id: str
    strategy_type: str
    params: dict[str, Any]
    trade_mode: str = "long_only"
    candidate_key: str | None = None
    position_model: str = "single_side"
    supports_vectorized_signals: bool = False
    source_pool: str = LAB_STRATEGY_SOURCE_REGISTRY
    source_stage: str | None = None
    display_name: str | None = None


def _supports_vectorized_signals(strategy: BaseStrategy) -> bool:
    return type(strategy).generate_signals is not BaseStrategy.generate_signals


def _load_strategy_candidates(
    strategy_ids: list[str] | None = None,
    strategy_sources: list[str] | None = None,
    max_strategies: int | None = None,
) -> list[StrategyCandidate]:
    id_filter = {str(value).strip() for value in (strategy_ids or []) if str(value).strip()}
    sources = normalize_strategy_sources(strategy_sources)
    rows: list[StrategyCandidate] = []
    seen_ids: set[str] = set()

    registry_strategies: dict[str, BaseStrategy] | None = None

    def _append_registry_candidates() -> None:
        nonlocal registry_strategies
        if registry_strategies is None:
            discover()
            registry_strategies = get_all()
        for strategy_id, strategy in sorted(registry_strategies.items()):
            sid = str(strategy_id).strip()
            params = dict(strategy.params or {})
            if validate_backtest_risk_controls(params):
                continue
            for trade_mode in expand_strategy_trade_modes(
                strategy_type=str(strategy.strategy_type),
                params=params,
                strategy_obj=strategy,
            ):
                candidate_key = f"{sid}:{trade_mode}"
                if id_filter and sid not in id_filter and candidate_key not in id_filter:
                    continue
                if candidate_key in seen_ids:
                    continue
                display_name = str(getattr(strategy, "name", sid) or sid)
                if display_name and trade_mode != "long_only":
                    display_name = f"{display_name} [{trade_mode}]"
                rows.append(
                    StrategyCandidate(
                        strategy_id=sid,
                        strategy_type=str(strategy.strategy_type),
                        params=params,
                        trade_mode=trade_mode,
                        candidate_key=candidate_key,
                        position_model=("hedged" if trade_mode == "both" else "single_side"),
                        supports_vectorized_signals=_supports_vectorized_signals(strategy),
                        source_pool=LAB_STRATEGY_SOURCE_REGISTRY,
                        display_name=display_name,
                    )
                )
                seen_ids.add(candidate_key)
                if max_strategies is not None and len(rows) >= max(1, int(max_strategies)):
                    return

    def _append_managed_candidates(source: str) -> None:
        managed_candidates = list_strategy_pool_candidates(
            strategy_sources=[source],
            strategy_ids=list(id_filter) if id_filter else None,
        )
        for candidate in managed_candidates:
            sid = str(candidate.get("strategy_id") or "").strip()
            candidate_key = str(candidate.get("candidate_key") or f"{sid}:{candidate.get('trade_mode') or 'long_only'}").strip()
            if not sid or not candidate_key:
                continue
            if id_filter and sid not in id_filter and candidate_key not in id_filter:
                continue
            if candidate_key in seen_ids:
                continue
            params = dict(candidate.get("params") or {})
            if validate_backtest_risk_controls(params):
                continue
            rows.append(
                StrategyCandidate(
                    strategy_id=sid,
                    strategy_type=str(candidate.get("strategy_type") or ""),
                    params=params,
                    trade_mode=str(candidate.get("trade_mode") or "long_only"),
                    candidate_key=candidate_key,
                    position_model=str(candidate.get("position_model") or "single_side"),
                    supports_vectorized_signals=bool(candidate.get("supports_vectorized_signals")),
                    source_pool=str(candidate.get("source_pool") or ""),
                    source_stage=str(candidate.get("source_stage") or "") or None,
                    display_name=str(candidate.get("display_name") or sid),
                )
            )
            seen_ids.add(candidate_key)
            if max_strategies is not None and len(rows) >= max(1, int(max_strategies)):
                return

    for source in sources:
        if max_strategies is not None and len(rows) >= max(1, int(max_strategies)):
            break
        if source == LAB_STRATEGY_SOURCE_REGISTRY:
            _append_registry_candidates()
        else:
            _append_managed_candidates(source)

    if max_strategies is not None:
        rows = rows[: max(1, int(max_strategies))]
    return rows


def _resolve_period_bounds(
    snapshot: pd.DataFrame,
    *,
    period_start: str | None,
    period_end: str | None,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    start_ts = pd.to_datetime(period_start, utc=True, errors="coerce") if period_start else pd.NaT
    end_ts = pd.to_datetime(period_end, utc=True, errors="coerce") if period_end else pd.NaT
    snapshot_start = pd.Timestamp(snapshot["timestamp"].iloc[0])
    snapshot_end = pd.Timestamp(snapshot["timestamp"].iloc[-1])

    resolved_start = snapshot_start if pd.isna(start_ts) else max(snapshot_start, pd.Timestamp(start_ts))
    resolved_end = snapshot_end if pd.isna(end_ts) else min(snapshot_end, pd.Timestamp(end_ts))
    if resolved_end < resolved_start:
        return None, None
    return resolved_start, resolved_end


def _derive_train_test_bounds(
    snapshot: pd.DataFrame,
    *,
    experiment: Any,
) -> dict[str, tuple[pd.Timestamp | None, pd.Timestamp | None]]:
    timestamps = pd.DatetimeIndex(pd.to_datetime(snapshot["timestamp"], utc=True))
    snapshot_start = pd.Timestamp(timestamps[0])
    snapshot_end = pd.Timestamp(timestamps[-1])

    explicit_train = _resolve_period_bounds(
        snapshot,
        period_start=getattr(experiment, "train_start", None),
        period_end=getattr(experiment, "train_end", None),
    )
    explicit_test = _resolve_period_bounds(
        snapshot,
        period_start=getattr(experiment, "test_start", None),
        period_end=getattr(experiment, "test_end", None),
    )

    has_explicit_train = explicit_train != (snapshot_start, snapshot_end) or bool(
        getattr(experiment, "train_start", None) or getattr(experiment, "train_end", None)
    )
    has_explicit_test = explicit_test != (snapshot_start, snapshot_end) or bool(
        getattr(experiment, "test_start", None) or getattr(experiment, "test_end", None)
    )

    if has_explicit_train or has_explicit_test:
        train_start, train_end = explicit_train
        test_start, test_end = explicit_test
        if train_start is None or train_end is None:
            train_start = snapshot_start
            if test_start is not None:
                prior = timestamps[timestamps < test_start]
                train_end = pd.Timestamp(prior[-1]) if len(prior) else None
            else:
                train_end = snapshot_end
        if test_start is None or test_end is None:
            test_end = snapshot_end
            if train_end is not None:
                future = timestamps[timestamps > train_end]
                test_start = pd.Timestamp(future[0]) if len(future) else None
            else:
                test_start = snapshot_start
        return {
            "train": (train_start, train_end),
            "test": (test_start, test_end),
        }

    split_idx = max(1, min(len(timestamps) - 1, int(len(timestamps) * 0.70)))
    return {
        "train": (snapshot_start, pd.Timestamp(timestamps[split_idx - 1])),
        "test": (pd.Timestamp(timestamps[split_idx]), snapshot_end),
    }


def _load_execution_frame(*, experiment: Any, snapshot_manifest: Any) -> pd.DataFrame:
    execution_timeframe = experiment_execution_timeframe(experiment)
    regime_timeframe = experiment_regime_timeframe(experiment)

    if execution_timeframe == snapshot_manifest.timeframe:
        raw_execution = pd.read_parquet(snapshot_manifest.snapshot_path)
    else:
        raw_execution = load_parquet(experiment.symbol, execution_timeframe)
        if raw_execution is None or raw_execution.empty:
            raise ValueError(
                f"No execution dataset found for experiment {experiment.id} ({experiment.symbol} {execution_timeframe})"
            )

    execution_frame = _normalize_ohlcv_frame(raw_execution)
    train_start = getattr(experiment, "train_start", None)
    train_end = getattr(experiment, "train_end", None)
    test_start = getattr(experiment, "test_start", None)
    test_end = getattr(experiment, "test_end", None)
    period_start, period_end = _resolve_period_bounds(
        execution_frame,
        period_start=min([value for value in (train_start, test_start) if value], default=None),
        period_end=max([value for value in (train_end, test_end) if value], default=None),
    )
    if period_start is not None and period_end is not None:
        execution_frame = _slice_frame(execution_frame, period_start, period_end)
    if execution_frame.empty:
        raise ValueError(
            f"Execution frame is empty after clipping ({experiment.symbol} {execution_timeframe}, regime {regime_timeframe})"
        )
    return execution_frame


def _slice_frame(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    window = frame[(frame.index >= start) & (frame.index <= end)].copy()
    return window


def _collect_regime_windows(
    frame: pd.DataFrame,
    *,
    segments: list[Any],
    period_start: pd.Timestamp | None,
    period_end: pd.Timestamp | None,
) -> list[pd.DataFrame]:
    if period_start is None or period_end is None:
        return []
    windows: list[pd.DataFrame] = []
    for segment in segments:
        seg_start = pd.to_datetime(segment.segment_start, utc=True, errors="coerce")
        seg_end = pd.to_datetime(segment.segment_end, utc=True, errors="coerce")
        if pd.isna(seg_start) or pd.isna(seg_end):
            continue
        clipped_start = max(pd.Timestamp(seg_start), period_start)
        clipped_end = min(pd.Timestamp(seg_end), period_end)
        if clipped_end < clipped_start:
            continue
        window = _slice_frame(frame, clipped_start, clipped_end)
        if not window.empty:
            windows.append(window)
    return windows


def _project_regime_windows(
    frame: pd.DataFrame,
    *,
    segments: list[Any],
    period_start: pd.Timestamp | None,
    period_end: pd.Timestamp | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    windows = _collect_regime_windows(
        frame,
        segments=segments,
        period_start=period_start,
        period_end=period_end,
    )
    pooled = _pool_windows(windows)
    coverage = {
        "segment_count": int(len(segments)),
        "projected_window_count": int(len(windows)),
        "projected_bar_count": int(len(pooled)),
        "projected_window_start": (str(pooled["timestamp"].iloc[0]) if not pooled.empty else None),
        "projected_window_end": (str(pooled["timestamp"].iloc[-1]) if not pooled.empty else None),
    }
    return pooled, coverage


def _pool_windows(windows: list[pd.DataFrame]) -> pd.DataFrame:
    if not windows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    pooled = pd.concat(windows, ignore_index=True)
    return _normalize_ohlcv_frame(pooled)


@contextmanager
def _disable_backtest_production_persistence():
    import axiom.strategies.backtest as backtest_module

    original_sync = backtest_module._sync_strategy_metrics_and_promote_if_eligible
    original_remote = backtest_module._run_remote_backtest
    backtest_module._sync_strategy_metrics_and_promote_if_eligible = lambda *_args, **_kwargs: None
    backtest_module._run_remote_backtest = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        backtest_module._sync_strategy_metrics_and_promote_if_eligible = original_sync
        backtest_module._run_remote_backtest = original_remote


def _run_window_backtest(
    *,
    strategy: StrategyCandidate,
    window: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    min_bars = resolve_min_matrix_window_bars(timeframe)
    if len(window) < min_bars:
        return {"error": f"Insufficient bars for backtest ({len(window)} < {min_bars})"}
    if not strategy.supports_vectorized_signals and len(window) > MAX_NON_VECTORIZED_MATRIX_BARS:
        message = (
            "Skipped non-vectorized strategy on large Regime Lab window "
            f"({len(window)} bars > {MAX_NON_VECTORIZED_MATRIX_BARS})"
        )
        log.info(
            "Skipping Regime Lab backtest for %s (%s): %s",
            strategy.strategy_id,
            strategy.strategy_type,
            message,
        )
        return {"error": message}
    params = dict(strategy.params)
    params.setdefault("timeframe", timeframe)
    with _disable_backtest_production_persistence():
        return backtest_strategy(
            strategy_id=strategy.strategy_id,
            asset=symbol,
            strategy_type=strategy.strategy_type,
            params=params,
            bars=len(window),
            timeframe=timeframe,
            fee_bps=BASE_FEE_BPS,
            slippage_bps=BASE_SLIPPAGE_BPS,
            persist_legacy_run=False,
            candles_df=window,
            regime_gate=True,
            trade_mode=strategy.trade_mode,
        )


def _result_to_trade_metrics(result: dict[str, Any]) -> tuple[dict[str, float], list[float], dict[str, Any]]:
    if result.get("error"):
        return _metrics_from_trade_returns([]), [], {"status": "error", "error": str(result["error"])}

    trades = list(result.get("is_trades") or []) + list(result.get("oos_trades") or []) + list(result.get("trades") or [])
    unique_trades: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for trade in trades:
        key = (
            trade.get("entry_time"),
            trade.get("exit_time"),
            trade.get("entry_price"),
            trade.get("exit_price"),
            trade.get("pnl_pct"),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_trades.append(trade)

    returns = [float(trade.get("pnl_pct")) for trade in unique_trades if trade.get("pnl_pct") is not None]
    robustness_values = [
        float((result.get(name) or {}).get("robustness") or 0.0)
        for name in ("is_metrics", "oos_metrics", "metrics")
        if isinstance(result.get(name), dict)
    ]
    stability = float(np.mean(robustness_values)) if robustness_values else 0.0
    metrics = _metrics_from_trade_returns(returns, stability=stability)
    diagnostics = {
        "status": "ok",
        "trade_count": len(unique_trades),
        "window_bars": int(result.get("bars") or 0),
        "is_metrics": dict(result.get("is_metrics") or {}),
        "oos_metrics": dict(result.get("oos_metrics") or {}),
    }
    return metrics, returns, diagnostics


def _build_candidate_payload(
    *,
    regime: str,
    strategy: StrategyCandidate,
    train_window: pd.DataFrame,
    oos_window: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    min_bars = resolve_min_matrix_window_bars(timeframe)
    train_result = _run_window_backtest(
        strategy=strategy,
        window=train_window,
        symbol=symbol,
        timeframe=timeframe,
    )
    oos_result = _run_window_backtest(
        strategy=strategy,
        window=oos_window,
        symbol=symbol,
        timeframe=timeframe,
    )

    train_metrics, train_returns, train_diagnostics = _result_to_trade_metrics(train_result)
    oos_metrics, oos_returns, oos_diagnostics = _result_to_trade_metrics(oos_result)

    adjusted_train = apply_regime_execution_penalty(
        regime=regime,
        raw_metrics=train_metrics,
        trade_returns=train_returns,
        base_execution_cost_bps=BASE_EXECUTION_COST_BPS,
    )
    adjusted_oos = apply_regime_execution_penalty(
        regime=regime,
        raw_metrics=oos_metrics,
        trade_returns=oos_returns,
        base_execution_cost_bps=BASE_EXECUTION_COST_BPS,
    )

    train_trade_density = _trade_density(
        float(adjusted_train.get("total_trades") or 0.0),
        int(len(train_window)),
    )
    oos_trade_density = _trade_density(
        float(adjusted_oos.get("total_trades") or 0.0),
        int(len(oos_window)),
    )

    train_metrics["oos_forward_total_trades"] = float(oos_metrics.get("total_trades") or 0.0)
    train_metrics["oos_forward_total_return_pct"] = float(oos_metrics.get("total_return_pct") or 0.0)
    train_metrics["oos_forward_profit_factor"] = float(oos_metrics.get("profit_factor") or 0.0)
    train_metrics["trade_density_per_100_bars"] = train_trade_density
    train_metrics["oos_trade_density_per_100_bars"] = oos_trade_density
    adjusted_train["oos_forward_total_trades"] = float(adjusted_oos.get("total_trades") or 0.0)
    adjusted_train["oos_forward_total_return_pct"] = float(adjusted_oos.get("total_return_pct") or 0.0)
    adjusted_train["oos_forward_profit_factor"] = float(adjusted_oos.get("profit_factor") or 0.0)
    adjusted_train["trade_density_per_100_bars"] = train_trade_density
    adjusted_train["oos_trade_density_per_100_bars"] = oos_trade_density
    adjusted_oos["trade_density_per_100_bars"] = oos_trade_density

    admission = evaluate_admission_gates(adjusted_train)
    reasons: list[str] = []
    if len(train_window) < min_bars:
        reasons.append("insufficient_train_bars")
    if len(oos_window) < min_bars:
        reasons.append("insufficient_oos_bars")
    if train_result.get("error"):
        reasons.append("train_backtest_error")
        if "non-vectorized" in str(train_result.get("error") or "").lower():
            reasons.append("train_non_vectorized_large_window")
    if oos_result.get("error"):
        reasons.append("oos_backtest_error")
        if "non-vectorized" in str(oos_result.get("error") or "").lower():
            reasons.append("oos_non_vectorized_large_window")
    admission["reasons"] = reasons
    if reasons:
        admission["admitted"] = False

    return {
        "strategy_id": strategy.strategy_id,
        "candidate_key": strategy.candidate_key or f"{strategy.strategy_id}:{strategy.trade_mode}",
        "strategy_type": strategy.strategy_type,
        "trade_mode": strategy.trade_mode,
        "position_model": strategy.position_model,
        "strategy_source": strategy.source_pool,
        "strategy_stage": strategy.source_stage,
        "strategy_name": strategy.display_name,
        "raw_metrics": train_metrics,
        "adjusted_metrics": adjusted_train,
        "oos_raw_metrics": oos_metrics,
        "oos_adjusted_metrics": adjusted_oos,
        "admission": admission,
        "coverage": {
            "train_bars": int(len(train_window)),
            "oos_bars": int(len(oos_window)),
            "required_min_bars": int(min_bars),
            "train_window_start": (str(train_window["timestamp"].iloc[0]) if not train_window.empty else None),
            "train_window_end": (str(train_window["timestamp"].iloc[-1]) if not train_window.empty else None),
            "oos_window_start": (str(oos_window["timestamp"].iloc[0]) if not oos_window.empty else None),
            "oos_window_end": (str(oos_window["timestamp"].iloc[-1]) if not oos_window.empty else None),
        },
        "diagnostics": {
            "train": train_diagnostics,
            "oos": oos_diagnostics,
        },
    }


def _candidate_to_score_row(
    *,
    candidate: dict[str, Any],
    regime: str,
    regime_timeframe: str,
    execution_timeframe: str,
) -> dict[str, Any]:
    score_components = dict(candidate.get("score_components") or {})
    candidate_key = str(candidate.get("candidate_key") or f"{candidate['strategy_id']}:{candidate.get('trade_mode') or 'long_only'}")
    normalized_regime = normalize_core_regime(regime) or str(regime).strip().upper()
    return {
        "strategy_id": candidate_key,
        "regime": normalized_regime,
        "score": float(candidate.get("score") or 0.0),
        "metrics_json": {
            "raw": candidate["raw_metrics"],
            "adjusted": candidate["adjusted_metrics"],
            "oos_raw": candidate["oos_raw_metrics"],
            "oos_adjusted": candidate["oos_adjusted_metrics"],
            "coverage": candidate["coverage"],
            "diagnostics": candidate["diagnostics"],
            "strategy_meta": {
                "strategy_id": candidate["strategy_id"],
                "candidate_key": candidate_key,
                "trade_mode": candidate.get("trade_mode") or "long_only",
                "position_model": candidate.get("position_model") or "single_side",
                "source_pool": candidate["strategy_source"],
                "source_stage": candidate["strategy_stage"],
                "strategy_name": candidate["strategy_name"],
            },
            "timeframes": {
                "regime_timeframe": regime_timeframe,
                "execution_timeframe": execution_timeframe,
            },
        },
        "admission_json": {
            **candidate["admission"],
            "score_components": score_components,
        },
    }


def _score_row_to_candidate(row: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(row.get("metrics_json") or {})
    strategy_meta = dict(metrics.get("strategy_meta") or {})
    admission = dict(row.get("admission_json") or {})
    candidate_key = str(strategy_meta.get("candidate_key") or row["strategy_id"])
    return {
        "strategy_id": str(strategy_meta.get("strategy_id") or row["strategy_id"]),
        "candidate_key": candidate_key,
        "trade_mode": str(strategy_meta.get("trade_mode") or "long_only"),
        "position_model": str(strategy_meta.get("position_model") or "single_side"),
        "strategy_source": str(strategy_meta.get("source_pool") or ""),
        "strategy_stage": strategy_meta.get("source_stage"),
        "strategy_name": strategy_meta.get("strategy_name"),
        "raw_metrics": dict(metrics.get("raw") or {}),
        "adjusted_metrics": dict(metrics.get("adjusted") or {}),
        "oos_raw_metrics": dict(metrics.get("oos_raw") or {}),
        "oos_adjusted_metrics": dict(metrics.get("oos_adjusted") or {}),
        "coverage": dict(metrics.get("coverage") or {}),
        "diagnostics": dict(metrics.get("diagnostics") or {}),
        "admission": admission,
        "score_components": dict(admission.get("score_components") or {}),
        "score": float(row.get("score") or 0.0),
    }


class _WatchdogTimer:
    """Context manager that kills a ProcessPoolExecutor if no progress is made within timeout."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_WATCHDOG_STALL_TIMEOUT_SECONDS,
        pool: ProcessPoolExecutor | None = None,
        job_id: str = "",
        check_interval: float = 30.0,
        abort_event: "threading.Event | None" = None,
    ):
        self._timeout = max(30.0, float(timeout_seconds))
        self._pool = pool
        self._job_id = job_id
        self._check_interval = max(5.0, float(check_interval))
        self._abort_event = abort_event
        self._lock = threading.Lock()
        self._last_progress_at = time.time()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._timed_out = False

    def set_pool(self, pool: ProcessPoolExecutor) -> None:
        """Set the pool reference after construction (for use when pool is created inside the with block)."""
        self._pool = pool

    def ping(self) -> None:
        """Signal that progress was made."""
        with self._lock:
            self._last_progress_at = time.time()

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(min(self._check_interval, 5.0)):
            external_abort = self._abort_event is not None and self._abort_event.is_set()
            with self._lock:
                elapsed = time.time() - self._last_progress_at
            if external_abort or elapsed >= self._timeout:
                self._timed_out = True
                if external_abort:
                    log.critical(
                        "Watchdog: matrix job %s aborted by external timeout/heartbeat — killing pool",
                        self._job_id,
                    )
                else:
                    log.critical(
                        "Watchdog timeout: matrix job %s stalled for %.0fs with no progress — killing pool",
                        self._job_id,
                        elapsed,
                    )
                if self._pool is not None:
                    try:
                        self._pool.shutdown(wait=False, cancel_futures=True)
                    except Exception as exc:
                        log.warning("Watchdog pool shutdown error: %s", exc)
                break

    def __enter__(self):
        self._last_progress_at = time.time()
        self._stop_event.clear()
        self._timed_out = False
        self._thread = threading.Thread(
            target=self._watchdog_loop,
            name=f"watchdog-{self._job_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        return False


def _run_candidate_in_worker(item: dict[str, Any]) -> dict[str, Any]:
    """Top-level picklable worker for ProcessPoolExecutor."""
    return _build_candidate_payload(
        regime=item["regime"],
        strategy=item["strategy"],
        train_window=item["train_window"],
        oos_window=item["oos_window"],
        symbol=item["symbol"],
        timeframe=item["timeframe"],
    )


def _retry_candidate_in_process(
    item: dict[str, Any], first_exc: Exception, max_retries: int = MATRIX_CELL_MAX_RETRIES
) -> dict[str, Any] | None:
    """STRATEGY-LOSS fix (lab-matrix-retry): bounded in-process retry for a cell
    whose backtest raised a (presumed transient) exception in the worker.

    Re-runs the backtest synchronously up to ``max_retries`` times (overridable
    per-job via the ``matrix_cell_max_retries`` payload key; defaults to the
    module constant). Returns the candidate payload on success, or ``None`` if
    every retry also raised — the caller then records the honest
    not-admitted/worker_error result. A clean "strategy failed the backtest"
    outcome is NOT an exception (it comes back as a normal payload with
    admitted:False), so it never reaches here and is never retried.
    """
    s = item["strategy"]
    last_exc: Exception = first_exc
    for attempt in range(1, max_retries + 1):
        try:
            candidate = _run_candidate_in_worker(item)
            log.info(
                "Matrix cell %s/%s recovered on in-process retry %d/%d after error: %s",
                item["regime"], s.strategy_id, attempt, max_retries, last_exc,
            )
            return candidate
        except Exception as retry_exc:  # noqa: BLE001 — transient retry, bounded
            last_exc = retry_exc
            log.warning(
                "Matrix cell %s/%s retry %d/%d failed: %s",
                item["regime"], s.strategy_id, attempt, max_retries, retry_exc,
            )
    return None


REGIME_CHAMPION_APPROVAL_TYPE = "regime_champion_promotion"


def _detect_champion_changes(
    *,
    model_version_id: str,
    container_payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare proposed champions against existing containers. Return list of changes."""
    existing = list_regime_container_snapshots(model_version_id)
    existing_champions: dict[str, str | None] = {}
    for container in existing:
        regime = str(container.get("regime") or "").upper()
        champion = container.get("champion") or {}
        existing_champions[regime] = str(champion.get("strategy_id") or "").strip() or None

    changes: list[dict[str, Any]] = []
    for payload in container_payloads:
        regime = str(payload["regime"]).upper()
        new_champion = payload.get("champion") or {}
        new_champion_id = str(new_champion.get("strategy_id") or "").strip() or None
        old_champion_id = existing_champions.get(regime)
        if new_champion_id != old_champion_id and new_champion_id is not None:
            changes.append({
                "regime": regime,
                "old_champion_strategy_id": old_champion_id,
                "new_champion_strategy_id": new_champion_id,
                "new_champion_score": float(new_champion.get("score") or 0.0),
            })
    return changes


def _create_champion_approval(
    *,
    program_id: str | None,
    model_version_id: str,
    score_version: str,
    container_payloads: list[dict[str, Any]],
    champion_changes: list[dict[str, Any]],
) -> int:
    """Create a pending approval record for champion promotion. Returns approval ID."""
    from axiom.db import create_approval, log_activity

    regime_summaries = [
        f"{change['regime']}: {change.get('old_champion_strategy_id') or '(none)'} → {change['new_champion_strategy_id']}"
        for change in champion_changes
    ]
    reason = f"Regime champion change proposed for {len(champion_changes)} regime(s): {'; '.join(regime_summaries)}"

    approval_id = create_approval(
        approval_type=REGIME_CHAMPION_APPROVAL_TYPE,
        target_type="regime_container",
        target_id=model_version_id,
        requested_status="active",
        status="pending_approval",
        actor="lab_matrix_engine",
        reason=reason,
        payload={
            "program_id": program_id,
            "model_version_id": model_version_id,
            "score_version": score_version,
            "container_payloads": container_payloads,
            "champion_changes": champion_changes,
        },
        owner="ceo",
    )

    log_activity(
        "info",
        "lab_matrix_engine",
        f"Regime champion promotion pending approval #{approval_id}: {reason}",
        {"approval_id": approval_id, "model_version_id": model_version_id, "changes": champion_changes},
    )
    return approval_id


def apply_champion_promotion(approval_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the container write after human approval. Called from approvals.py."""
    program_id = payload.get("program_id")
    model_version_id = str(payload["model_version_id"])
    score_version = str(payload.get("score_version") or "v1")
    container_payloads = list(payload["container_payloads"])

    containers_persisted = replace_regime_containers(
        program_id=program_id,
        model_version_id=model_version_id,
        score_version=score_version,
        regimes=container_payloads,
    )
    return {
        "containers_persisted": containers_persisted,
        "model_version_id": model_version_id,
        "champion_changes": payload.get("champion_changes") or [],
    }


def run_matrix_job(
    job_id: str,
    *,
    worker_id: str | None = None,
    lease_seconds: int = 90,
    abort_event: "threading.Event | None" = None,
) -> dict[str, Any]:
    """Execute one matrix job. Must be called only for a claimed RUNNING queue job."""
    job = get_lab_job(job_id)
    if job is None:
        raise ValueError(f"Unknown lab job: {job_id}")
    if job.job_type != MATRIX_JOB_TYPE:
        raise ValueError(f"Unsupported job type for matrix runner: {job.job_type}")
    if job.state != LabJobState.RUNNING:
        raise RuntimeError("Matrix runner requires a claimed RUNNING queue job context")

    payload = dict(job.payload_json or {})
    model_version_id = str(payload.get("model_version_id") or "").strip()
    if not model_version_id:
        raise ValueError("Matrix job payload missing model_version_id")

    model_version = get_model_version(model_version_id)
    if model_version is None:
        raise ValueError(f"Unknown model version: {model_version_id}")
    if not model_version.experiment_id:
        raise ValueError(f"Model version {model_version_id} is not linked to an experiment")

    experiment = get_lab_experiment(model_version.experiment_id)
    if experiment is None:
        raise ValueError(f"Unknown experiment for model version: {model_version_id}")
    program_id = str(
        payload.get("program_id")
        or model_version.program_id
        or experiment.program_id
        or job.program_id
        or ""
    ).strip() or None
    cycle_id = str(payload.get("cycle_id") or "").strip() or None

    snapshot_manifest = get_snapshot_manifest(model_version.experiment_id)
    if snapshot_manifest is None:
        raise ValueError(f"No snapshot manifest found for experiment: {model_version.experiment_id}")

    raw_snapshot = pd.read_parquet(snapshot_manifest.snapshot_path)
    snapshot = _normalize_ohlcv_frame(raw_snapshot)
    execution_frame = _load_execution_frame(experiment=experiment, snapshot_manifest=snapshot_manifest)
    regime_timeframe = experiment_regime_timeframe(experiment)
    execution_timeframe = experiment_execution_timeframe(experiment)
    segments = get_regime_segments(model_version_id=model_version_id, timeframe=regime_timeframe)
    if not segments:
        raise ValueError(f"No regime segments found for model version: {model_version_id}")

    strategy_sources = normalize_strategy_sources(payload.get("strategy_sources"))
    strategies = _load_strategy_candidates(
        strategy_ids=payload.get("strategy_ids"),
        strategy_sources=strategy_sources,
        max_strategies=payload.get("max_strategies"),
    )
    if not strategies:
        raise ValueError("No strategies available for matrix run")

    regime_to_segments: dict[str, list[Any]] = {regime: [] for regime in REGIME_TAXONOMY}
    for segment in segments:
        normalized_regime = normalize_core_regime(segment.regime)
        if normalized_regime is None:
            continue
        regime_to_segments.setdefault(normalized_regime, []).append(segment)

    periods = _derive_train_test_bounds(snapshot, experiment=experiment)
    train_start, train_end = periods["train"]
    oos_start, oos_end = periods["test"]

    score_rows: list[dict[str, Any]] = []
    latest_cycle_score_rows: list[dict[str, Any]] = []
    container_payloads: list[dict[str, Any]] = []
    score_version = str(payload.get("score_version") or "v1")
    reserve_count = max(1, int(payload.get("reserve_count") or DEFAULT_RESERVE_COUNT))
    min_champion_dwell_hours = max(1, int(payload.get("min_champion_dwell_hours") or DEFAULT_MIN_CHAMPION_DWELL_HOURS))
    min_champion_score_delta = max(0.0, float(payload.get("min_champion_score_delta") or DEFAULT_MIN_CHAMPION_SCORE_DELTA))
    graveyard_required_wins = max(1, int(payload.get("graveyard_required_wins") or DEFAULT_GRAVEYARD_REQUIRED_WINS))
    total_steps = max(1, len(REGIME_TAXONOMY) * len(strategies))
    completed_steps = 0
    matrix_workers = max(1, int(payload.get("matrix_workers") or 1))
    # Overridable per-job; defaults to the module constant (mirrors the adjacent
    # blacklist_* payload knobs). 0 disables the in-process retry entirely.
    max_cell_retries = max(0, int(payload.get("matrix_cell_max_retries", MATRIX_CELL_MAX_RETRIES)))
    watchdog_timeout = max(30, int(payload.get("watchdog_stall_timeout_seconds") or DEFAULT_WATCHDOG_STALL_TIMEOUT_SECONDS))

    # Pre-compute all regime windows once (cheap, sequential)
    regime_windows_map: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for regime in REGIME_TAXONOMY:
        segment_rows = regime_to_segments.get(regime, [])
        train_window, _ = _project_regime_windows(
            execution_frame,
            segments=segment_rows,
            period_start=train_start,
            period_end=train_end,
        )
        oos_window, _ = _project_regime_windows(
            execution_frame,
            segments=segment_rows,
            period_start=oos_start,
            period_end=oos_end,
        )
        regime_windows_map[regime] = (train_window, oos_window)

    # Filter out blacklisted strategies
    try:
        blacklisted_ids = get_blacklisted_strategy_ids()
    except Exception:
        blacklisted_ids = set()
    if blacklisted_ids:
        pre_filter_count = len(strategies)
        strategies = [s for s in strategies if s.strategy_id not in blacklisted_ids]
        filtered_count = pre_filter_count - len(strategies)
        if filtered_count:
            log.info("Blacklist filter: excluded %d strategies from matrix (%d remaining)", filtered_count, len(strategies))

    # Flat work list: one item per (regime, strategy) pair
    work_items: list[dict[str, Any]] = [
        {
            "regime": regime,
            "strategy": strategy,
            "train_window": regime_windows_map[regime][0],
            "oos_window": regime_windows_map[regime][1],
            "symbol": snapshot_manifest.symbol,
            "timeframe": execution_timeframe,
        }
        for regime in REGIME_TAXONOMY
        for strategy in strategies
    ]

    # Execute backtests — parallel when matrix_workers > 1
    regime_candidates_map: dict[str, list[dict[str, Any]]] = {r: [] for r in REGIME_TAXONOMY}
    if matrix_workers > 1 and len(work_items) > 1:
        with _WatchdogTimer(
            timeout_seconds=watchdog_timeout, job_id=job_id, abort_event=abort_event
        ) as watchdog:
            with ProcessPoolExecutor(max_workers=matrix_workers) as pool:
                watchdog.set_pool(pool)
                futures: dict = {pool.submit(_run_candidate_in_worker, item): item for item in work_items}
                try:
                    completed_iter = as_completed(futures, timeout=watchdog_timeout)
                except TypeError:
                    # Fallback for Python < 3.9 where timeout isn't supported
                    completed_iter = as_completed(futures)
                for future in completed_iter:
                    # Fast interruption: a job-level timeout / heartbeat escalation
                    # (relayed via abort_event) cancels remaining cells instead of
                    # waiting for the whole batch.
                    if abort_event is not None and abort_event.is_set():
                        log.error("Matrix job %s aborted (timeout/heartbeat) — cancelling pool", job_id)
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise WatchdogTimeoutError(
                            f"Matrix job {job_id} aborted by job-timeout/heartbeat signal"
                        )
                    item = futures[future]
                    try:
                        result = future.result(timeout=120)
                    except Exception as exc:
                        s = item["strategy"]
                        log.warning("Parallel backtest worker error %s/%s: %s", item["regime"], s.strategy_id, exc)
                        # STRATEGY-LOSS fix (lab-matrix-retry): a bounded in-process
                        # retry rescues a single TRANSIENT (IO/connection) blip so it
                        # can't permanently drop a viable challenger. A TIMEOUT or a
                        # dead pool is NOT transient: retrying re-runs the slow
                        # backtest on the main thread (stalling the matrix watchdog)
                        # and would let a chronically-slow strategy evade the timeout
                        # blacklist — so for those we record the timeout and accept
                        # not-admitted directly.
                        _exc_s = str(exc)
                        is_timeout = any(kw in _exc_s for kw in ("timed out", "timeout", "TimeoutError"))
                        is_pool_dead = "BrokenProcessPool" in _exc_s or "process pool" in _exc_s.lower()
                        result = None if (is_timeout or is_pool_dead) else _retry_candidate_in_process(item, exc, max_retries=max_cell_retries)
                        if result is None:
                            if not (is_timeout or is_pool_dead):
                                log.warning(
                                    "DROPPED matrix cell %s/%s as not-admitted after %d retries — "
                                    "transient worker error never cleared: %s",
                                    item["regime"], s.strategy_id, MATRIX_CELL_MAX_RETRIES, exc,
                                )
                            result = {
                                "strategy_id": s.strategy_id,
                                "candidate_key": s.candidate_key or f"{s.strategy_id}:{s.trade_mode}",
                                "strategy_type": s.strategy_type,
                                "trade_mode": s.trade_mode,
                                "position_model": getattr(s, "position_model", None),
                                "strategy_source": getattr(s, "source_pool", None),
                                "strategy_stage": getattr(s, "source_stage", None),
                                "strategy_name": getattr(s, "display_name", s.strategy_id),
                                "raw_metrics": {},
                                "adjusted_metrics": {},
                                "oos_raw_metrics": {},
                                "oos_adjusted_metrics": {},
                                "admission": {"admitted": False, "reasons": ["worker_error"]},
                                "coverage": {},
                                "diagnostics": {},
                            }
                            # Record timeout for blacklist tracking (now reached on
                            # the first timeout, since timeouts skip the retry).
                            if is_timeout:
                                try:
                                    record_strategy_timeout(
                                        s.strategy_id,
                                        threshold=int(payload.get("blacklist_timeout_threshold", 3)),
                                        expiry_days=int(payload.get("blacklist_expiry_days", 7)),
                                    )
                                except Exception:
                                    pass
                    regime_candidates_map[item["regime"]].append(result)
                    completed_steps += 1
                    watchdog.ping()
                    if worker_id:
                        s = item["strategy"]
                        heartbeat_lab_job(
                            job_id,
                            worker_id=worker_id,
                            lease_seconds=lease_seconds,
                            progress_json={
                                "phase": "matrix",
                                "completed_steps": completed_steps,
                                "total_steps": total_steps,
                                "regime": item["regime"],
                                "strategy_id": s.strategy_id,
                                "candidate_key": s.candidate_key or f"{s.strategy_id}:{s.trade_mode}",
                                "trade_mode": s.trade_mode,
                            },
                        )
            # Cancel any remaining futures and clean up
            timed_out_count = sum(1 for f in futures if not f.done())
            if timed_out_count:
                log.warning("Matrix job %s: %d futures still pending after completion loop — cancelling", job_id, timed_out_count)
                for f in futures:
                    if not f.done():
                        f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
            if watchdog.timed_out:
                raise WatchdogTimeoutError(f"Matrix job {job_id} stalled — watchdog killed pool after {watchdog_timeout}s with no progress")
    else:
        with _WatchdogTimer(
            timeout_seconds=watchdog_timeout, job_id=job_id, abort_event=abort_event
        ) as watchdog:
            for item in work_items:
                if watchdog.timed_out:
                    raise WatchdogTimeoutError(f"Matrix job {job_id} stalled during sequential execution")
                if abort_event is not None and abort_event.is_set():
                    raise WatchdogTimeoutError(
                        f"Matrix job {job_id} aborted by job-timeout/heartbeat signal"
                    )
                try:
                    candidate = _build_candidate_payload(
                        regime=item["regime"],
                        strategy=item["strategy"],
                        train_window=item["train_window"],
                        oos_window=item["oos_window"],
                        symbol=item["symbol"],
                        timeframe=item["timeframe"],
                    )
                except Exception as exc:
                    s = item["strategy"]
                    log.warning("Sequential backtest error %s/%s: %s", item["regime"], s.strategy_id, exc)
                    # STRATEGY-LOSS fix (lab-matrix-retry): retry only TRANSIENT
                    # (IO/connection) errors; a timeout/dead-pool skips the retry so
                    # it can't stall the watchdog or evade the timeout blacklist.
                    _exc_s = str(exc)
                    is_timeout = any(kw in _exc_s for kw in ("timed out", "timeout", "TimeoutError"))
                    is_pool_dead = "BrokenProcessPool" in _exc_s or "process pool" in _exc_s.lower()
                    candidate = None if (is_timeout or is_pool_dead) else _retry_candidate_in_process(item, exc, max_retries=max_cell_retries)
                    if candidate is None:
                        if not (is_timeout or is_pool_dead):
                            log.warning(
                                "DROPPED matrix cell %s/%s as not-admitted after %d retries — "
                                "transient backtest error never cleared: %s",
                                item["regime"], s.strategy_id, MATRIX_CELL_MAX_RETRIES, exc,
                            )
                        candidate = {
                            "strategy_id": s.strategy_id,
                            "candidate_key": s.candidate_key or f"{s.strategy_id}:{s.trade_mode}",
                            "strategy_type": s.strategy_type,
                            "trade_mode": s.trade_mode,
                            "position_model": getattr(s, "position_model", None),
                            "strategy_source": getattr(s, "source_pool", None),
                            "strategy_stage": getattr(s, "source_stage", None),
                            "strategy_name": getattr(s, "display_name", s.strategy_id),
                            "raw_metrics": {},
                            "adjusted_metrics": {},
                            "oos_raw_metrics": {},
                            "oos_adjusted_metrics": {},
                            "admission": {"admitted": False, "reasons": ["worker_error"]},
                            "coverage": {},
                            "diagnostics": {},
                        }
                        # Record timeout for blacklist tracking (now reached on the
                        # first timeout, since timeouts skip the retry).
                        if is_timeout:
                            try:
                                bl_threshold = int(payload.get("blacklist_timeout_threshold", 3))
                                bl_expiry = int(payload.get("blacklist_expiry_days", 7))
                                record_strategy_timeout(s.strategy_id, threshold=bl_threshold, expiry_days=bl_expiry)
                            except Exception:
                                pass
                regime_candidates_map[item["regime"]].append(candidate)
                completed_steps += 1
                watchdog.ping()
                if worker_id:
                    s = item["strategy"]
                    heartbeat_lab_job(
                        job_id,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                        progress_json={
                            "phase": "matrix",
                            "completed_steps": completed_steps,
                            "total_steps": total_steps,
                            "regime": item["regime"],
                            "strategy_id": s.strategy_id,
                            "candidate_key": s.candidate_key or f"{s.strategy_id}:{s.trade_mode}",
                            "trade_mode": s.trade_mode,
                        },
                    )

    # Score and rank per regime (sequential, cheap in-memory work)
    for regime in REGIME_TAXONOMY:
        regime_candidates = regime_candidates_map[regime]
        ranked_candidates, ranking_mode = _ranked_selection_pool(regime_candidates)
        scored = _score_admitted_candidates(ranked_candidates)
        scored_by_id = {_strategy_identity(row): row for row in scored}

        for candidate in regime_candidates:
            scored_candidate = scored_by_id.get(_strategy_identity(candidate))
            score_value = float(scored_candidate["score"]) if scored_candidate else 0.0
            score_components = scored_candidate.get("score_components") if scored_candidate else None
            enriched_candidate = dict(candidate)
            enriched_candidate["score"] = score_value
            enriched_candidate["score_components"] = score_components or {}
            enriched_admission = dict(enriched_candidate.get("admission") or {})
            enriched_admission["ranking_mode"] = ranking_mode if scored_candidate is not None else None
            enriched_candidate["admission"] = enriched_admission
            latest_cycle_score_rows.append(
                _candidate_to_score_row(
                    candidate=enriched_candidate,
                    regime=regime,
                    regime_timeframe=regime_timeframe,
                    execution_timeframe=execution_timeframe,
                )
            )

    if program_id:
        append_strategy_regime_observations(
            program_id=program_id,
            cycle_id=cycle_id,
            model_version_id=model_version_id,
            symbol=snapshot_manifest.symbol,
            timeframe=execution_timeframe,
            rows=[
                {
                    "strategy_id": row["strategy_id"],
                    "regime": row["regime"],
                    "score": row["score"],
                    "source_pool": dict((row.get("metrics_json") or {}).get("strategy_meta") or {}).get("source_pool"),
                    "metrics_json": row["metrics_json"],
                    "admission_json": row["admission_json"],
                }
                for row in latest_cycle_score_rows
            ],
        )
        score_rows = [
            {
                "strategy_id": observation.strategy_id,
                "regime": observation.regime,
                "score": observation.score,
                "metrics_json": dict(observation.metrics_json or {}),
                "admission_json": dict(observation.admission_json or {}),
            }
            for observation in list_latest_strategy_regime_observations(
                program_id=program_id,
                model_version_id=model_version_id,
            )
        ]
    else:
        score_rows = list(latest_cycle_score_rows)

    candidates_by_regime: dict[str, list[dict[str, Any]]] = {regime: [] for regime in REGIME_TAXONOMY}
    for row in score_rows:
        normalized_regime = normalize_core_regime(row.get("regime"))
        if normalized_regime is None:
            continue
        candidates_by_regime.setdefault(normalized_regime, []).append(_score_row_to_candidate(row))

    for regime in REGIME_TAXONOMY:
        aggregated_candidates = candidates_by_regime.get(regime, [])
        eligible_candidates, candidate_pool_mode = _ranked_selection_pool(aggregated_candidates)
        scored = sorted(
            eligible_candidates,
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )
        for rank, item in enumerate(scored, start=1):
            item["rank"] = rank

        members = [
            {
                "strategy_id": row["candidate_key"],
                "rank": int(row["rank"]),
                "score": float(row["score"]),
                "metrics_json": {
                    "raw": row["raw_metrics"],
                    "adjusted": row["adjusted_metrics"],
                    "oos_raw": row["oos_raw_metrics"],
                    "oos_adjusted": row["oos_adjusted_metrics"],
                    "coverage": row["coverage"],
                    "diagnostics": row["diagnostics"],
                    "strategy_meta": {
                        "strategy_id": row["strategy_id"],
                        "candidate_key": row["candidate_key"],
                        "trade_mode": row.get("trade_mode") or "long_only",
                        "position_model": row.get("position_model") or "single_side",
                        "source_pool": row["strategy_source"],
                        "source_stage": row["strategy_stage"],
                        "strategy_name": row["strategy_name"],
                    },
                    "score_components": row["score_components"],
                    "timeframes": {
                        "regime_timeframe": regime_timeframe,
                        "execution_timeframe": execution_timeframe,
                    },
                },
                "admitted": True,
            }
            for row in scored
        ]
        previous_snapshot = (
            _get_current_snapshot_compatible(model_version_id=model_version_id, regime=regime)
            if program_id
            else (
                _get_previous_snapshot_compatible(
                    experiment_id=model_version.experiment_id,
                    regime=regime,
                    exclude_model_version_id=model_version_id,
                )
                if model_version.experiment_id
                else None
            )
        )
        champion, reserves, selection_meta = _select_champion_with_guardrails(
            regime=regime,
            scored=scored,
            previous_snapshot=previous_snapshot,
            reserve_count=reserve_count,
            min_champion_dwell_hours=min_champion_dwell_hours,
            min_champion_score_delta=min_champion_score_delta,
            graveyard_required_wins=graveyard_required_wins,
        )
        selection_meta["candidate_pool_mode"] = candidate_pool_mode
        if champion is not None:
            rationale = dict(champion.get("rationale_json") or {})
            rationale["admission_mode"] = candidate_pool_mode
            champion["rationale_json"] = rationale
        container_payloads.append(
            {
                "regime": regime,
                "meta_json": {
                    "evaluated_strategies": len(aggregated_candidates),
                    "admitted_strategies": sum(
                        1
                        for candidate in aggregated_candidates
                        if bool(dict(candidate.get("admission") or {}).get("admitted"))
                    ),
                    "fallback_eligible_strategies": sum(
                        1
                        for candidate in aggregated_candidates
                        if bool(dict(candidate.get("admission") or {}).get("fallback_eligible"))
                    ),
                    "borderline_eligible_strategies": sum(
                        1
                        for candidate in aggregated_candidates
                        if bool(dict(candidate.get("admission") or {}).get("borderline_eligible"))
                    ),
                    "candidate_pool_mode": candidate_pool_mode,
                    "ranked_strategies": len(scored),
                    "strategy_sources": list(strategy_sources),
                    "reserve_count": reserve_count,
                    "train_bars": max(
                        [int((candidate.get("coverage") or {}).get("train_bars") or 0) for candidate in aggregated_candidates],
                        default=0,
                    ),
                    "oos_bars": max(
                        [int((candidate.get("coverage") or {}).get("oos_bars") or 0) for candidate in aggregated_candidates],
                        default=0,
                    ),
                    "regime_timeframe": regime_timeframe,
                    "execution_timeframe": execution_timeframe,
                    "champion_selected_at": selection_meta.get("champion_selected_at"),
                    "pending_graveyard_candidate_id": selection_meta.get("pending_graveyard_candidate_id"),
                    "pending_graveyard_wins": selection_meta.get("pending_graveyard_wins"),
                    "champion_selection": selection_meta,
                    "reserves": reserves,
                    "graveyard_resurrections": selection_meta.get("graveyard_resurrections") or [],
                    "observation_mode": "cumulative" if program_id else "single_run",
                },
                "members": members,
                "champion": champion,
            }
        )

    scores_persisted = replace_strategy_regime_scores(
        model_version_id=model_version_id,
        symbol=snapshot_manifest.symbol,
        timeframe=execution_timeframe,
        rows=score_rows,
    )
    champion_changes = _detect_champion_changes(
        model_version_id=model_version_id,
        container_payloads=container_payloads,
    )
    approval_id: int | None = None
    containers_persisted = 0
    if champion_changes:
        approval_id = _create_champion_approval(
            program_id=program_id,
            model_version_id=model_version_id,
            score_version=score_version,
            container_payloads=container_payloads,
            champion_changes=champion_changes,
        )
        log.info(
            "Champion promotion requires approval (approval #%s, %d regime(s) changed)",
            approval_id,
            len(champion_changes),
        )
    else:
        containers_persisted = replace_regime_containers(
            program_id=program_id,
            model_version_id=model_version_id,
            score_version=score_version,
            regimes=container_payloads,
        )
    update_lab_experiment_status(model_version.experiment_id, "matrix_ready")

    return {
        "status": "ok",
        "job_id": job_id,
        "model_version_id": model_version_id,
        "scores_persisted": scores_persisted,
        "containers_persisted": containers_persisted,
        "pending_approval_id": approval_id,
        "champion_changes": champion_changes or [],
        "evaluated_strategies": len(strategies),
        "aggregated_strategies": len({str(row["strategy_id"]) for row in score_rows}),
        "aggregated_base_strategies": len(
            {
                str(dict((row.get("metrics_json") or {}).get("strategy_meta") or {}).get("strategy_id") or row["strategy_id"])
                for row in score_rows
            }
        ),
        "evaluated_regimes": len(container_payloads),
        "strategy_sources": list(strategy_sources),
        "program_id": program_id,
        "cycle_id": cycle_id,
        "reserve_count": reserve_count,
        "regime_timeframe": regime_timeframe,
        "execution_timeframe": execution_timeframe,
        "train_start": (train_start.isoformat() if train_start is not None else None),
        "train_end": (train_end.isoformat() if train_end is not None else None),
        "oos_start": (oos_start.isoformat() if oos_start is not None else None),
        "oos_end": (oos_end.isoformat() if oos_end is not None else None),
        "completed_at": _now_iso(),
    }


def process_next_matrix_job(
    *,
    worker_id: str = "inline-matrix-worker",
    lease_seconds: int = 90,
) -> dict[str, Any] | None:
    """Compatibility helper for tests/manual runs; claims one queued job and processes it."""
    job = claim_next_lab_job(
        worker_id=worker_id,
        job_type=MATRIX_JOB_TYPE,
        lease_seconds=lease_seconds,
    )
    if job is None:
        return None

    try:
        summary = run_matrix_job(job.id, worker_id=worker_id, lease_seconds=lease_seconds)
        set_lab_job_state(
            job.id,
            state=LabJobState.SUCCEEDED,
            progress_json={"phase": "completed", **summary},
        )
        return summary
    except Exception as exc:
        refreshed = get_lab_job(job.id)
        attempts = int(refreshed.attempts if refreshed else job.attempts)
        max_attempts = int(refreshed.max_attempts if refreshed else job.max_attempts)
        terminal_state = LabJobState.DEADLETTER if attempts >= max_attempts else LabJobState.FAILED
        set_lab_job_state(
            job.id,
            state=terminal_state,
            error_json={"error": str(exc), "worker_id": worker_id},
            deadletter_reason=("max_attempts_exceeded" if terminal_state == LabJobState.DEADLETTER else None),
            progress_json={"phase": "failed", "error": str(exc)},
        )
        if job.experiment_id:
            update_lab_experiment_status(job.experiment_id, "matrix_failed")
        raise
