"""Phase 5 selector decision engine with cold-start guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from axiom.data import load_parquet
from axiom.lab_db import (
    create_selection_event,
    get_active_regime_program,
    get_lab_experiment,
    get_latest_model_version,
    get_model_version,
    get_regime_program,
    get_regime_container_snapshot,
    get_regime_segments,
    get_snapshot_manifest,
)
from axiom.lab_models import (
    SelectorDecideRequest,
    SelectorDecisionResponse,
)
from axiom.lab_regime_engine import (
    MIN_HYSTERESIS_DWELL_BARS,
    REGIME_TAXONOMY,
    TRANSITION_OVERLAY,
    classify_features,
    compute_features_from_frame,
    experiment_execution_timeframe,
    experiment_regime_timeframe,
    normalize_classifier_type,
    normalize_core_regime,
    regime_query_aliases,
)

DEFAULT_MIN_CONFIDENCE = 0.55
SELECTOR_MAX_BARS = 700
MAX_RAW_TRANSITION_SHARE = 0.50
MAX_RAW_SWITCH_RATE = 0.50


@dataclass
class _ModelContext:
    model_version_id: str
    experiment_id: str | None
    symbol: str
    regime_timeframe: str
    execution_timeframe: str
    snapshot_path: str | None
    classifier_type: str
    classifier_config: dict[str, Any]


def _resolve_context(request: SelectorDecideRequest) -> _ModelContext:
    if request.model_version_id:
        model_version = get_model_version(request.model_version_id)
    elif request.program_id:
        program = get_regime_program(request.program_id)
        model_version = get_model_version(program.active_model_version_id) if program and program.active_model_version_id else None
    else:
        active_program = get_active_regime_program()
        model_version = (
            get_model_version(active_program.active_model_version_id)
            if active_program and active_program.active_model_version_id
            else get_latest_model_version()
        )
    if model_version is None:
        raise ValueError("No lab model version is available yet")

    experiment = get_lab_experiment(model_version.experiment_id) if model_version.experiment_id else None
    snapshot = get_snapshot_manifest(model_version.experiment_id) if model_version.experiment_id else None
    classifier_payload = dict((model_version.config_json or {}).get("classifier") or {})

    symbol = (request.symbol or (experiment.symbol if experiment else "")).strip()
    regime_timeframe = (
        request.timeframe
        or (experiment_regime_timeframe(experiment) if experiment else "")
    ).strip()
    execution_timeframe = experiment_execution_timeframe(experiment) if experiment else regime_timeframe
    if not symbol or not regime_timeframe:
        raise ValueError("Selector requires symbol and timeframe")

    return _ModelContext(
        model_version_id=model_version.id,
        experiment_id=model_version.experiment_id,
        symbol=symbol,
        regime_timeframe=regime_timeframe,
        execution_timeframe=execution_timeframe,
        snapshot_path=(snapshot.snapshot_path if snapshot else None),
        classifier_type=normalize_classifier_type(classifier_payload.get("type")),
        classifier_config=dict(classifier_payload.get("config") or {}),
    )


def _resolve_market_frame(symbol: str, timeframe: str, snapshot_path: str | None) -> pd.DataFrame:
    frame = load_parquet(symbol, timeframe)
    if frame is not None and not frame.empty:
        return frame.tail(SELECTOR_MAX_BARS).reset_index(drop=True)
    if snapshot_path and Path(snapshot_path).exists():
        snapshot = pd.read_parquet(snapshot_path)
        if not snapshot.empty:
            return snapshot.tail(SELECTOR_MAX_BARS).reset_index(drop=True)
    raise ValueError(f"No market data available for selector ({symbol} {timeframe})")


def _slim_meta(meta: dict[str, Any]) -> dict[str, Any]:
    output = dict(meta)
    components = output.get("components")
    if isinstance(components, dict):
        if str((output.get("classifier") or {}).get("type") or "").strip().lower() == "gmm_v1":
            output["components"] = {
                "mode": components.get("mode"),
                "mapped_regime": components.get("mapped_regime"),
                "posterior_max": components.get("posterior_max"),
                "posterior_entropy": components.get("posterior_entropy"),
                "uncertain": components.get("uncertain"),
            }
        else:
            output["components"] = {
                "trend_state": components.get("trend_state"),
                "vol_state": components.get("vol_state"),
                "structure_state": components.get("structure_state"),
            }
    return output


def _switch_rate(values: list[str]) -> float:
    cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
    if len(cleaned) < 2:
        return 0.0
    changes = sum(1 for idx in range(1, len(cleaned)) if cleaned[idx] != cleaned[idx - 1])
    return float(changes / max(1, len(cleaned) - 1))


def _normalize_overlay_regime(value: object) -> str:
    core_regime = normalize_core_regime(value)
    if core_regime is not None:
        return core_regime
    normalized = str(value or "").strip().upper()
    return normalized or TRANSITION_OVERLAY


def _is_uncertain_bar(raw_regime: object, raw_meta: dict[str, Any] | None) -> bool:
    overlay = str((raw_meta or {}).get("overlay_regime") or "").strip().upper()
    normalized = str(raw_regime or "").strip().upper()
    return bool((raw_meta or {}).get("uncertain")) or overlay == TRANSITION_OVERLAY or normalized == TRANSITION_OVERLAY


def _get_container_snapshot_compatible(model_version_id: str, regime: str) -> dict[str, Any] | None:
    for alias in regime_query_aliases(regime):
        container = get_regime_container_snapshot(model_version_id=model_version_id, regime=alias)
        if container is not None:
            return container
    return None


def _derive_uncertainty_state(
    classified: pd.DataFrame,
    *,
    confidence: float,
    min_confidence: float,
    stability_ok: bool,
) -> dict[str, Any]:
    recent = classified.tail(MIN_HYSTERESIS_DWELL_BARS).reset_index(drop=True)
    latest = recent.iloc[-1]
    raw_meta = dict(latest.get("raw_meta") or {})
    regime = _normalize_overlay_regime(latest["regime"])
    raw_regime = _normalize_overlay_regime(latest.get("raw_regime") or regime)

    raw_regimes = [_normalize_overlay_regime(value) for value in recent["raw_regime"].tolist()]
    resolved_regimes = [_normalize_overlay_regime(value) for value in recent["regime"].tolist()]
    raw_uncertain_flags = [
        _is_uncertain_bar(raw_regime_value, dict(raw_meta_value or {}))
        for raw_regime_value, raw_meta_value in zip(recent["raw_regime"].tolist(), recent["raw_meta"].tolist(), strict=False)
    ]
    raw_uncertain_share = float(sum(1 for flag in raw_uncertain_flags if flag) / max(1, len(raw_uncertain_flags)))
    raw_switch_rate = _switch_rate(raw_regimes)
    resolved_switch_rate = _switch_rate(resolved_regimes)
    classifier_uncertain = _is_uncertain_bar(raw_regime, raw_meta)
    uncertain_state = classifier_uncertain
    low_confidence = confidence < min_confidence

    uncertain_regime = bool(
        low_confidence
        or (not stability_ok)
        or raw_uncertain_share > MAX_RAW_TRANSITION_SHARE
        or raw_switch_rate > MAX_RAW_SWITCH_RATE
    )

    return {
        "raw_regime": raw_regime,
        "uncertain_state": uncertain_state,
        "transition_state": uncertain_state,
        "classifier_uncertain": classifier_uncertain,
        "raw_uncertain_share": round(raw_uncertain_share, 6),
        "raw_transition_share": round(raw_uncertain_share, 6),
        "raw_switch_rate": round(raw_switch_rate, 6),
        "resolved_switch_rate": round(resolved_switch_rate, 6),
        "low_confidence": low_confidence,
        "uncertain_regime": uncertain_regime,
    }


def decide_current_regime(request: SelectorDecideRequest) -> SelectorDecisionResponse:
    context = _resolve_context(request)
    frame = _resolve_market_frame(context.symbol, context.regime_timeframe, context.snapshot_path)
    features = compute_features_from_frame(frame)
    if features.empty:
        raise ValueError("Selector feature frame is empty")
    classified = classify_features(
        features,
        classifier_type=context.classifier_type,
        classifier_config=context.classifier_config,
    )
    if classified.empty:
        raise ValueError("Selector classifier produced no labels")

    latest = classified.iloc[-1]
    regime = _normalize_overlay_regime(latest["regime"])
    confidence = float(latest["confidence"])
    raw_confidence = float(latest.get("raw_confidence") or confidence)
    min_confidence = max(0.0, float(request.min_confidence or DEFAULT_MIN_CONFIDENCE))

    recent = classified.tail(MIN_HYSTERESIS_DWELL_BARS)
    stability_ok = len(recent) >= MIN_HYSTERESIS_DWELL_BARS and int(recent["regime"].nunique()) == 1
    confidence_ok = confidence >= min_confidence
    uncertainty_state = _derive_uncertainty_state(
        classified,
        confidence=confidence,
        min_confidence=min_confidence,
        stability_ok=stability_ok,
    )
    transition_state = bool(uncertainty_state["transition_state"])
    uncertain_regime = bool(uncertainty_state["uncertain_regime"])

    segments = get_regime_segments(
        model_version_id=context.model_version_id,
        symbol=context.symbol,
        timeframe=context.regime_timeframe,
    )
    seen_regimes = {
        normalized_regime
        for segment in segments
        if str(segment.timeframe or "").strip() == context.regime_timeframe
        if (normalized_regime := normalize_core_regime(segment.regime)) is not None
    }
    unseen_regime = regime not in seen_regimes and regime in REGIME_TAXONOMY

    blocked_reason: str | None = None
    decision = "trade"
    champion_strategy_id: str | None = None
    champion_meta: dict[str, Any] = {}

    if regime not in REGIME_TAXONOMY:
        decision = "no_trade"
        blocked_reason = "no_trade:uncertain_regime" if regime == TRANSITION_OVERLAY else "no_trade:cold_start"
    elif unseen_regime:
        decision = "no_trade"
        blocked_reason = "no_trade:cold_start"
    elif uncertain_regime or (not confidence_ok):
        decision = "no_trade"
        blocked_reason = "no_trade:uncertain_regime"
    else:
        container = _get_container_snapshot_compatible(
            model_version_id=context.model_version_id,
            regime=regime,
        )
        champion = (container or {}).get("champion") or {}
        champion_strategy_id = str(champion.get("strategy_id") or "").strip() or None
        champion_meta = dict(champion.get("rationale_json") or {})
        for member in list((container or {}).get("members") or []):
            if str(member.get("strategy_id") or "").strip() != champion_strategy_id:
                continue
            metrics = dict(member.get("metrics_json") or {})
            strategy_meta = dict(metrics.get("strategy_meta") or {})
            if strategy_meta:
                champion_meta = {
                    **strategy_meta,
                    **champion_meta,
                }
            break
        if champion_strategy_id is None:
            decision = "no_trade"
            blocked_reason = "no_trade:no_champion"

    selection_event = create_selection_event(
        symbol=context.symbol,
        timeframe=context.regime_timeframe,
        regime=regime,
        confidence=confidence,
        champion_strategy_id=champion_strategy_id,
        blocked_reason=blocked_reason,
        decision_json={
            "decision": decision,
            "model_version_id": context.model_version_id,
            "raw_confidence": raw_confidence,
            "min_confidence": min_confidence,
            "stability_ok": stability_ok,
            "confidence_ok": confidence_ok,
            "unseen_regime": unseen_regime,
            "uncertain_regime": uncertain_regime,
            "uncertain_state": transition_state,
            "transition_state": transition_state,
            "raw_regime": str(uncertainty_state["raw_regime"]),
            "classifier_uncertain": bool(uncertainty_state["classifier_uncertain"]),
            "raw_uncertain_share": float(uncertainty_state["raw_uncertain_share"]),
            "raw_transition_share": float(uncertainty_state["raw_transition_share"]),
            "raw_switch_rate": float(uncertainty_state["raw_switch_rate"]),
            "resolved_switch_rate": float(uncertainty_state["resolved_switch_rate"]),
            "regime_timeframe": context.regime_timeframe,
            "execution_timeframe": context.execution_timeframe,
            "classifier_type": context.classifier_type,
            "champion_meta": champion_meta,
            "meta_json": _slim_meta(dict(latest.get("meta_json") or {})),
        },
    )

    return SelectorDecisionResponse(
        status="ok",
        model_version_id=context.model_version_id,
        symbol=context.symbol,
        timeframe=context.regime_timeframe,
        regime_timeframe=context.regime_timeframe,
        execution_timeframe=context.execution_timeframe,
        decision=decision,
        regime=regime,
        confidence=confidence,
        champion_strategy_id=champion_strategy_id,
        blocked_reason=blocked_reason,
        selection_event_id=selection_event.id,
        meta_json={
            "raw_confidence": raw_confidence,
            "min_confidence": min_confidence,
            "stability_ok": stability_ok,
            "confidence_ok": confidence_ok,
            "unseen_regime": unseen_regime,
            "uncertain_regime": uncertain_regime,
            "uncertain_state": transition_state,
            "transition_state": transition_state,
            "raw_regime": str(uncertainty_state["raw_regime"]),
            "classifier_uncertain": bool(uncertainty_state["classifier_uncertain"]),
            "raw_uncertain_share": float(uncertainty_state["raw_uncertain_share"]),
            "raw_transition_share": float(uncertainty_state["raw_transition_share"]),
            "raw_switch_rate": float(uncertainty_state["raw_switch_rate"]),
            "resolved_switch_rate": float(uncertainty_state["resolved_switch_rate"]),
            "bars_evaluated": int(len(classified)),
            "latest_timestamp": str(latest["timestamp"]),
            "regime_timeframe": context.regime_timeframe,
            "execution_timeframe": context.execution_timeframe,
            "classifier_type": context.classifier_type,
            "champion_meta": champion_meta,
            "taxonomy": list(REGIME_TAXONOMY),
        },
    )
