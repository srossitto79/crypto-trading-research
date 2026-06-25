"""Regime Lab API routing (Phase 3: snapshots + regime engine)."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from axiom.api_security import require_operator_access

from axiom.lab_db import (
    create_lab_experiment,
    enqueue_lab_job,
    get_active_regime_program,
    get_lab_experiment,
    get_lab_job,
    get_latest_discovery_cycle,
    get_latest_model_version,
    get_latest_job_for_experiment,
    get_program_cycle_stats,
    get_regime_labels,
    get_regime_program,
    get_regime_segments,
    get_model_version,
    list_regime_programs,
    list_model_versions,
    list_regime_container_snapshots,
    list_strategy_regime_scores,
    get_regime_container_snapshot,
    get_snapshot_manifest,
    init_lab_db,
    list_lab_job_events,
    list_lab_jobs,
    update_regime_program,
    upsert_lab_experiment,
    upsert_regime_program,
)
from axiom.lab_intent_dispatch import dispatch_paper_intent
from axiom.lab_matrix_engine import MATRIX_JOB_TYPE
from axiom.lab_orchestrator import (
    derive_train_test_window,
    enqueue_continuous_cycle,
    get_orchestrator_active_jobs,
    get_orchestrator_config,
    get_orchestrator_program_bundle,
    get_orchestrator_status,
    inspect_current_strategy_pool,
    update_orchestrator_config,
)
from axiom.lab_strategy_pool import inspect_strategy_pool
from axiom.lab_regime_engine import (
    MODEL_REBUILD_JOB_TYPE,
    REGIME_TAXONOMY,
    SEGMENT_BUILD_JOB_TYPE,
    TRANSITION_OVERLAY,
    normalize_core_regime,
    run_model_rebuild,
    run_segment_build,
)
from axiom.lab_worker_service import get_lab_worker_status, read_lab_worker_feed, start_lab_worker_process
from axiom.lab_models import (
    DEFAULT_EXECUTION_TIMEFRAME,
    DEFAULT_REGIME_TIMEFRAME,
    BacktestMatrixEnqueueResponse,
    BacktestMatrixRequest,
    ContainerRebuildRequest,
    ContinuousCycleEnqueueResponse,
    ContinuousOrchestratorStatusResponse,
    ContinuousOrchestratorUpdateRequest,
    CreateExperimentRequest,
    CreateExperimentResponse,
    DispatchPaperIntentRequest,
    DispatchPaperIntentResponse,
    ExperimentStatusResponse,
    LabJobState,
    ModelRebuildRequest,
    ModelRebuildEnqueueResponse,
    ModelRebuildResponse,
    InitializeRegimeProgramRequest,
    InitializeRegimeProgramResponse,
    RegimeProgramListResponse,
    RegimeProgramResponse,
    SegmentBuildRequest,
    SegmentBuildEnqueueResponse,
    SegmentBuildResponse,
    SelectorDecideRequest,
    SelectorDecisionResponse,
    QueueJobListResponse,
    StrategyPoolReportResponse,
)
from axiom.lab_selector import decide_current_regime

router = APIRouter(prefix="/api/lab/regime", tags=["lab_regime"], dependencies=[Depends(require_operator_access)])
log = logging.getLogger("axiom.routers.lab_regime")

_INIT_LOCK = threading.Lock()
_LAB_READY = False


def _heatmap_source_priority(source_pool: str | None) -> int:
    order = {
        "active": 0,
        "paper": 1,
        "backtesting": 2,
        "registry": 3,
        "graveyard": 4,
    }
    return order.get(str(source_pool or "").strip().lower(), 9)


def _derive_heatmap_cell_state(
    *,
    score: float,
    admission: dict[str, object],
    diagnostics: dict[str, object],
) -> tuple[str, str | None]:
    admitted = bool(admission.get("admitted"))
    reasons = [str(reason).strip() for reason in list(admission.get("reasons") or []) if str(reason).strip()]
    train_diag = dict(diagnostics.get("train") or {})
    oos_diag = dict(diagnostics.get("oos") or {})
    errors = [str(value).strip() for value in (train_diag.get("error"), oos_diag.get("error")) if str(value or "").strip()]

    if any("Insufficient bars for backtest" in error for error in errors) or any(
        reason in {"insufficient_train_bars", "insufficient_oos_bars"} for reason in reasons
    ):
        return "insufficient_data", (errors[0] if errors else (reasons[0] if reasons else None))
    if errors or train_diag.get("status") == "error" or oos_diag.get("status") == "error":
        return "error", (errors[0] if errors else (reasons[0] if reasons else None))
    if admitted:
        return "admitted", None
    if float(score or 0.0) > 0.0:
        return "scored", (reasons[0] if reasons else None)
    return "rejected", (reasons[0] if reasons else None)


def _regime_order(value: object) -> tuple[int, str]:
    normalized = str(value or "").strip().upper()
    try:
        return REGIME_TAXONOMY.index(normalized), normalized
    except ValueError:
        if normalized == TRANSITION_OVERLAY:
            return len(REGIME_TAXONOMY), normalized
        return len(REGIME_TAXONOMY) + 1, normalized


def _normalized_report_regime(
    regime: object,
    *,
    meta_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stored_regime = str(regime or "").strip().upper()
    meta = dict(meta_json or {})
    components = dict(meta.get("components") or {})
    raw_regime = str(meta.get("raw_regime") or stored_regime).strip().upper()
    mapped_regime = str(components.get("mapped_regime") or "").strip().upper()
    core_regime = (
        normalize_core_regime(stored_regime)
        or normalize_core_regime(raw_regime)
        or normalize_core_regime(mapped_regime)
    )
    overlay_regime = str(components.get("overlay_regime") or "").strip().upper() or None
    uncertain = bool(components.get("uncertain")) or overlay_regime == TRANSITION_OVERLAY
    if stored_regime == TRANSITION_OVERLAY or raw_regime == TRANSITION_OVERLAY:
        uncertain = True
        overlay_regime = overlay_regime or TRANSITION_OVERLAY
    display_regime = core_regime or (raw_regime if raw_regime and raw_regime != TRANSITION_OVERLAY else stored_regime)
    if not display_regime:
        display_regime = stored_regime or "UNKNOWN"
    return {
        "stored_regime": stored_regime,
        "raw_regime": raw_regime or None,
        "core_regime": core_regime,
        "display_regime": display_regime,
        "uncertain": uncertain,
        "overlay_regime": overlay_regime,
        "legacy_regime": (stored_regime if core_regime and stored_regime != core_regime else None),
    }


def _timeline_segment_payload(segment: Any) -> dict[str, Any]:
    meta_json = dict(segment.meta_json or {})
    regime_payload = _normalized_report_regime(segment.regime, meta_json=meta_json)
    uncertain_share = meta_json.get("uncertain_share")
    if uncertain_share is None:
        uncertain_share = 0.0
    return {
        **segment.model_dump(),
        **regime_payload,
        "uncertain_share": float(uncertain_share or 0.0),
    }


def _timeline_label_payload(label: Any) -> dict[str, Any]:
    meta_json = dict(label.meta_json or {})
    regime_payload = _normalized_report_regime(label.regime, meta_json=meta_json)
    return {
        **label.model_dump(),
        **regime_payload,
    }


def _timeline_price_payloads(*, model: Any, timeline_labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not timeline_labels:
        return []

    model_config = dict(getattr(model, "config_json", {}) or {})
    snapshot_path = str(model_config.get("snapshot_path") or "").strip()
    if not snapshot_path and getattr(model, "experiment_id", None):
        manifest = get_snapshot_manifest(str(model.experiment_id))
        snapshot_path = str(manifest.snapshot_path or "").strip() if manifest is not None else ""
    if not snapshot_path or not Path(snapshot_path).exists():
        return []

    try:
        frame = pd.read_parquet(snapshot_path, columns=["timestamp", "close"])
    except Exception:
        log.warning("Regime Lab timeline could not read snapshot prices from %s", snapshot_path, exc_info=True)
        return []

    if frame.empty or "timestamp" not in frame.columns or "close" not in frame.columns:
        return []

    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
    if frame.empty:
        return []

    label_frame = pd.DataFrame(
        {
            "label_ts": [str(label.get("ts") or "") for label in timeline_labels],
            "timestamp": pd.to_datetime([label.get("ts") for label in timeline_labels], utc=True, errors="coerce"),
        }
    ).dropna(subset=["timestamp"])
    if label_frame.empty:
        return []

    merged = label_frame.merge(frame, on="timestamp", how="left").sort_values("timestamp")
    merged["close"] = merged["close"].ffill().bfill()
    merged = merged.dropna(subset=["close"])
    if merged.empty:
        return []

    base_close = float(merged["close"].iloc[0])
    if not pd.notna(base_close) or abs(base_close) < 1e-12:
        return []

    payloads: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        close_value = float(row["close"])
        payloads.append(
            {
                "ts": str(row["label_ts"]),
                "close": close_value,
                "normalized_close": close_value / base_close,
                "return_pct": ((close_value / base_close) - 1.0) * 100.0,
            }
        )
    return payloads


def _heatmap_candidate_rank(cell: dict[str, Any]) -> tuple[int, float, int]:
    state_rank = {
        "admitted": 0,
        "scored": 1,
        "rejected": 2,
        "insufficient_data": 3,
        "error": 4,
    }.get(str(cell.get("state") or ""), 9)
    score = float(cell.get("score") or 0.0)
    source_pool = str(dict(cell.get("strategy_meta") or {}).get("source_pool") or "")
    return state_rank, -score, _heatmap_source_priority(source_pool)


def _ensure_lab_ready() -> None:
    global _LAB_READY
    if _LAB_READY:
        return
    with _INIT_LOCK:
        if _LAB_READY:
            return
        init_lab_db()
        _LAB_READY = True


def _resolve_request_timeframes(body: CreateExperimentRequest) -> tuple[str, str]:
    fields_set = set(getattr(body, "model_fields_set", set()) or set())
    alias_timeframe = (body.timeframe or "").strip()
    explicit_regime = "regime_timeframe" in fields_set
    explicit_execution = "execution_timeframe" in fields_set

    if alias_timeframe and not explicit_regime:
        regime_timeframe = alias_timeframe
    else:
        regime_timeframe = (body.regime_timeframe or alias_timeframe or DEFAULT_REGIME_TIMEFRAME).strip()

    if alias_timeframe and not explicit_execution:
        execution_timeframe = alias_timeframe
    else:
        execution_timeframe = (body.execution_timeframe or regime_timeframe or DEFAULT_EXECUTION_TIMEFRAME).strip()

    return regime_timeframe or DEFAULT_REGIME_TIMEFRAME, execution_timeframe or regime_timeframe or DEFAULT_EXECUTION_TIMEFRAME


def _maybe_start_continuous_worker(*, auto_start_worker: bool) -> None:
    if not auto_start_worker:
        return
    try:
        start_lab_worker_process()
    except Exception as exc:
        log.warning("Regime Lab could not auto-start worker: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to auto-start Regime Lab worker: {exc}") from None


def _resolve_active_program():
    bundle = get_orchestrator_program_bundle()
    program = bundle.get("program")
    return program if program is not None else get_active_regime_program()


def _resolve_preferred_model_version_id(model_version_id: str | None = None, *, program_id: str | None = None) -> str | None:
    if model_version_id:
        return model_version_id
    if program_id:
        program = get_regime_program(program_id)
        if program and program.active_model_version_id:
            return program.active_model_version_id
    active_program = _resolve_active_program()
    if active_program and active_program.active_model_version_id:
        return active_program.active_model_version_id
    latest_model = get_latest_model_version()
    return latest_model.id if latest_model else None


def _find_active_program_setup_job(*, program_id: str, experiment_id: str | None = None):
    matches = []
    for job in list_lab_jobs(states=[LabJobState.QUEUED, LabJobState.RUNNING], limit=500):
        if job.job_type not in {MODEL_REBUILD_JOB_TYPE, SEGMENT_BUILD_JOB_TYPE}:
            continue
        if str(job.program_id or "").strip() != str(program_id).strip():
            continue
        payload = dict(job.payload_json or {})
        if not bool(payload.get("program_initialize")):
            continue
        payload_experiment_id = str(payload.get("experiment_id") or "").strip() or None
        if experiment_id and payload_experiment_id and payload_experiment_id != experiment_id:
            continue
        if experiment_id and job.experiment_id and str(job.experiment_id).strip() != experiment_id:
            continue
        matches.append(job)
    for preferred_state in (LabJobState.RUNNING, LabJobState.QUEUED):
        for job in matches:
            if job.state == preferred_state:
                return job
    return None


@router.get("/programs", response_model=RegimeProgramListResponse)
def get_programs(limit: int = Query(default=50, ge=1, le=500)):
    _ensure_lab_ready()
    programs = list_regime_programs(limit=limit)
    return RegimeProgramListResponse(status="ok", programs=programs, total=len(programs))


@router.get("/programs/active", response_model=RegimeProgramResponse)
def get_active_program():
    _ensure_lab_ready()
    program = _resolve_active_program()
    active_model = get_model_version(program.active_model_version_id) if program and program.active_model_version_id else None
    last_cycle = get_latest_discovery_cycle(program.id) if program else None
    cycle_stats = get_program_cycle_stats(program.id) if program else {}
    return RegimeProgramResponse(
        status="ok",
        program=program,
        active_model=active_model,
        last_cycle=last_cycle,
        cycle_stats=cycle_stats,
    )


@router.post("/programs", response_model=RegimeProgramResponse)
def post_program(body: InitializeRegimeProgramRequest):
    _ensure_lab_ready()
    program = upsert_regime_program(
        program_id=body.program_id,
        symbol=body.symbol,
        regime_timeframe=body.regime_timeframe,
        execution_timeframe=body.execution_timeframe,
        status="active",
        config_json={
            "classifier_type": body.classifier_type,
            "classifier_config": dict(body.classifier_config or {}),
        },
        notes=body.notes,
    )
    update_orchestrator_config(
        {
            "program_id": program.id,
            "symbol": program.symbol,
            "regime_timeframe": program.regime_timeframe,
            "execution_timeframe": program.execution_timeframe,
            "classifier_type": body.classifier_type,
            "classifier_config": dict(body.classifier_config or {}),
        }
    )
    return RegimeProgramResponse(
        status="ok",
        program=program,
        active_model=(get_model_version(program.active_model_version_id) if program.active_model_version_id else None),
        last_cycle=get_latest_discovery_cycle(program.id),
        cycle_stats=get_program_cycle_stats(program.id),
    )


@router.post("/programs/initialize", response_model=InitializeRegimeProgramResponse)
def post_initialize_program(body: InitializeRegimeProgramRequest):
    _ensure_lab_ready()
    program = upsert_regime_program(
        program_id=body.program_id,
        symbol=body.symbol,
        regime_timeframe=body.regime_timeframe,
        execution_timeframe=body.execution_timeframe,
        status="active",
        config_json={
            "classifier_type": body.classifier_type,
            "classifier_config": dict(body.classifier_config or {}),
        },
        notes=body.notes,
    )
    config = update_orchestrator_config(
        {
            "program_id": program.id,
            "symbol": program.symbol,
            "regime_timeframe": program.regime_timeframe,
            "execution_timeframe": program.execution_timeframe,
            "classifier_type": body.classifier_type,
            "classifier_config": dict(body.classifier_config or {}),
        }
    )
    if body.train_start or body.train_end or body.test_start or body.test_end:
        train_start = body.train_start
        train_end = body.train_end
        test_start = body.test_start
        test_end = body.test_end
    else:
        window = derive_train_test_window(
            now=datetime.now(UTC),
            train_lookback_days=int(config.get("train_lookback_days", 365) or 365),
            oos_lookback_days=int(config.get("oos_lookback_days", 365) or 365),
        )
        train_start = window["train_start"]
        train_end = window["train_end"]
        test_start = window["test_start"]
        test_end = window["test_end"]
    experiment_id = str(program.active_experiment_id or f"exp_prog_{program.id}")
    experiment = upsert_lab_experiment(
        experiment_id=experiment_id,
        program_id=program.id,
        symbol=program.symbol,
        timeframe=program.regime_timeframe,
        regime_timeframe=program.regime_timeframe,
        execution_timeframe=program.execution_timeframe,
        train_start=train_start.isoformat() if train_start else None,
        train_end=train_end.isoformat() if train_end else None,
        test_start=test_start.isoformat() if test_start else None,
        test_end=test_end.isoformat() if test_end else None,
        notes=body.notes or "Program initialization",
        status="queued",
    )
    update_regime_program(program.id, active_experiment_id=experiment.id, status="running")
    _maybe_start_continuous_worker(auto_start_worker=bool(config.get("auto_start_worker", True)))
    existing_job = _find_active_program_setup_job(program_id=program.id, experiment_id=experiment.id)
    if existing_job is not None:
        return InitializeRegimeProgramResponse(
            status=("already_running" if existing_job.state == LabJobState.RUNNING else "already_queued"),
            program_id=program.id,
            experiment_id=experiment.id,
            rebuild_job_id=existing_job.id,
            rebuild_job_state=existing_job.state,
            queued_at=existing_job.created_at,
        )
    rebuild_job = enqueue_lab_job(
        job_type=MODEL_REBUILD_JOB_TYPE,
        program_id=program.id,
        experiment_id=experiment.id,
        payload={
            "program_id": program.id,
            "experiment_id": experiment.id,
            "classifier_type": str(config.get("classifier_type") or body.classifier_type),
            "classifier_config": dict(config.get("classifier_config") or body.classifier_config or {}),
            "program_initialize": True,
            "auto_enqueue_segments": True,
            "min_segment_bars": int(config.get("min_segment_bars", 24) or 24),
            "notes": body.notes or "Program initialization baseline rebuild",
        },
    )
    return InitializeRegimeProgramResponse(
        status="queued",
        program_id=program.id,
        experiment_id=experiment.id,
        rebuild_job_id=rebuild_job.id,
        rebuild_job_state=rebuild_job.state,
        queued_at=rebuild_job.created_at,
    )


@router.post("/experiments", response_model=CreateExperimentResponse)
def create_experiment(body: CreateExperimentRequest):
    _ensure_lab_ready()
    regime_timeframe, execution_timeframe = _resolve_request_timeframes(body)
    experiment_id = f"exp_{uuid4().hex[:10]}"
    experiment = create_lab_experiment(
        experiment_id=experiment_id,
        program_id=body.program_id,
        symbol=body.symbol,
        timeframe=regime_timeframe,
        regime_timeframe=regime_timeframe,
        execution_timeframe=execution_timeframe,
        train_start=body.train_start.isoformat() if body.train_start else None,
        train_end=body.train_end.isoformat() if body.train_end else None,
        test_start=body.test_start.isoformat() if body.test_start else None,
        test_end=body.test_end.isoformat() if body.test_end else None,
        notes=body.notes,
        status="queued",
    )
    job = enqueue_lab_job(
        job_type="experiment_create",
        program_id=body.program_id,
        experiment_id=experiment_id,
        payload={
            "symbol": experiment.symbol,
            "timeframe": experiment.timeframe,
            "regime_timeframe": experiment.regime_timeframe,
            "execution_timeframe": experiment.execution_timeframe,
            "train_start": experiment.train_start,
            "train_end": experiment.train_end,
            "test_start": experiment.test_start,
            "test_end": experiment.test_end,
            "notes": experiment.notes,
        },
    )
    return CreateExperimentResponse(
        status="queued",
        experiment_id=experiment_id,
        job_id=job.id,
        job_state=job.state,
        queued_at=job.created_at,
        regime_timeframe=experiment.regime_timeframe,
        execution_timeframe=experiment.execution_timeframe,
    )


@router.get("/experiments/{experiment_id}", response_model=ExperimentStatusResponse)
def get_experiment_status(experiment_id: str):
    _ensure_lab_ready()
    experiment = get_lab_experiment(experiment_id)
    if experiment is None:
        return ExperimentStatusResponse(status="unknown_experiment", experiment_id=experiment_id, latest_job=None)
    snapshot = get_snapshot_manifest(experiment_id)
    latest = get_latest_job_for_experiment(experiment_id)
    if latest is not None:
        status = str(latest.state.value)
    else:
        status = experiment.status
    return ExperimentStatusResponse(
        status=status,
        experiment_id=experiment_id,
        experiment=experiment,
        snapshot=snapshot,
        latest_job=latest,
    )


@router.post("/model/rebuild", response_model=ModelRebuildResponse)
def post_model_rebuild(body: ModelRebuildRequest):
    _ensure_lab_ready()
    try:
        return run_model_rebuild(body)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "Unknown experiment" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from None


@router.post("/model/rebuild/enqueue", response_model=ModelRebuildEnqueueResponse)
def post_model_rebuild_enqueue(body: ModelRebuildRequest):
    _ensure_lab_ready()
    experiment = get_lab_experiment(body.experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"Unknown experiment: {body.experiment_id}")
    job = enqueue_lab_job(
        job_type=MODEL_REBUILD_JOB_TYPE,
        program_id=body.program_id or experiment.program_id,
        experiment_id=body.experiment_id,
        payload=body.model_dump(),
    )
    return ModelRebuildEnqueueResponse(
        status="queued",
        experiment_id=body.experiment_id,
        job_id=job.id,
        job_state=job.state,
        queued_at=job.created_at,
    )


@router.post("/segments/build", response_model=SegmentBuildResponse)
def post_segments_build(body: SegmentBuildRequest):
    _ensure_lab_ready()
    try:
        return run_segment_build(body)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "Unknown model version" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from None


@router.post("/segments/build/enqueue", response_model=SegmentBuildEnqueueResponse)
def post_segments_build_enqueue(body: SegmentBuildRequest):
    _ensure_lab_ready()
    model_version = get_model_version(body.model_version_id)
    if model_version is None:
        raise HTTPException(status_code=404, detail=f"Unknown model version: {body.model_version_id}")
    job = enqueue_lab_job(
        job_type=SEGMENT_BUILD_JOB_TYPE,
        experiment_id=model_version.experiment_id,
        payload=body.model_dump(),
    )
    return SegmentBuildEnqueueResponse(
        status="queued",
        model_version_id=body.model_version_id,
        job_id=job.id,
        job_state=job.state,
        queued_at=job.created_at,
    )


@router.post("/backtests/matrix", response_model=BacktestMatrixEnqueueResponse)
def post_backtests_matrix(body: BacktestMatrixRequest):
    _ensure_lab_ready()
    model_version = get_model_version(body.model_version_id)
    if model_version is None:
        raise HTTPException(status_code=404, detail=f"Unknown model version: {body.model_version_id}")
    if not model_version.experiment_id:
        raise HTTPException(
            status_code=400,
            detail=f"Model version is not linked to an experiment: {body.model_version_id}",
        )
    job = enqueue_lab_job(
        job_type=MATRIX_JOB_TYPE,
        program_id=body.program_id or model_version.program_id,
        experiment_id=model_version.experiment_id,
        payload=body.model_dump(),
    )
    return BacktestMatrixEnqueueResponse(
        status="queued",
        model_version_id=body.model_version_id,
        job_id=job.id,
        job_state=job.state,
        queued_at=job.created_at,
    )


@router.get("/orchestrator/status", response_model=ContinuousOrchestratorStatusResponse)
def get_continuous_orchestrator_status():
    _ensure_lab_ready()
    bundle = get_orchestrator_program_bundle()
    return ContinuousOrchestratorStatusResponse(
        status="ok",
        config=get_orchestrator_config(),
        orchestrator=get_orchestrator_status(),
        active_jobs=get_orchestrator_active_jobs(),
        program=bundle["program"],
        last_cycle=bundle["last_cycle"],
        cycle_stats=bundle["cycle_stats"],
    )


@router.post("/orchestrator/configure", response_model=ContinuousOrchestratorStatusResponse)
def post_continuous_orchestrator_config(body: ContinuousOrchestratorUpdateRequest):
    _ensure_lab_ready()
    config = update_orchestrator_config(body.model_dump(exclude_none=True))
    if config.get("enabled"):
        _maybe_start_continuous_worker(auto_start_worker=bool(config.get("auto_start_worker", True)))
    if body.run_immediately:
        enqueue_continuous_cycle(reason="manual_configured_run", force=True)
    bundle = get_orchestrator_program_bundle()
    return ContinuousOrchestratorStatusResponse(
        status="ok",
        config=config,
        orchestrator=get_orchestrator_status(),
        active_jobs=get_orchestrator_active_jobs(),
        program=bundle["program"],
        last_cycle=bundle["last_cycle"],
        cycle_stats=bundle["cycle_stats"],
    )


@router.post("/orchestrator/run-now", response_model=ContinuousCycleEnqueueResponse)
def post_continuous_orchestrator_run_now():
    _ensure_lab_ready()
    config = get_orchestrator_config()
    _maybe_start_continuous_worker(auto_start_worker=bool(config.get("auto_start_worker", True)))
    payload = enqueue_continuous_cycle(reason="manual_run_now", force=True)
    if payload is None:
        raise HTTPException(status_code=409, detail="Continuous cycle could not be queued because another lab cycle is active")
    return ContinuousCycleEnqueueResponse(
        status="queued",
        job_id=str(payload["job_id"]),
        job_state=LabJobState(str(payload["job_state"])),
        queued_at=str(payload["queued_at"]),
        cycle_id=str(payload["cycle_id"]),
    )


@router.get("/pool", response_model=StrategyPoolReportResponse)
def get_strategy_pool(
    source: list[str] | None = Query(default=None),
):
    _ensure_lab_ready()
    payload = inspect_current_strategy_pool() if not source else inspect_strategy_pool(strategy_sources=source)
    return StrategyPoolReportResponse(status="ok", **payload)


@router.post("/containers/rebuild")
def post_containers_rebuild(body: ContainerRebuildRequest):
    _ensure_lab_ready()
    job = enqueue_lab_job(
        job_type="containers_rebuild",
        payload=body.model_dump(),
    )
    return {
        "status": "queued",
        "job_id": job.id,
        "job_state": job.state.value,
        "queued_at": job.created_at,
    }


@router.get("/containers/{regime}")
def get_container(regime: str, model_version_id: str | None = Query(default=None)):
    _ensure_lab_ready()
    model_version_id = _resolve_preferred_model_version_id(model_version_id)
    if model_version_id:
        payload = get_regime_container_snapshot(model_version_id=model_version_id, regime=regime)
        if payload is not None:
            return {"status": "ok", "regime": regime, **payload}
    return {
        "status": "ok",
        "regime": regime,
        "model_version_id": None,
        "score_version": None,
        "members": [],
        "champion": None,
        "reserves": [],
        "selection_evidence": {},
        "updated_at": None,
    }


@router.get("/containers")
def get_containers(model_version_id: str | None = Query(default=None)):
    _ensure_lab_ready()
    model_version_id = _resolve_preferred_model_version_id(model_version_id)
    if model_version_id is None:
        return {"status": "ok", "model_version_id": None, "containers": []}
    return {
        "status": "ok",
        "model_version_id": model_version_id,
        "containers": list_regime_container_snapshots(model_version_id),
    }


@router.get("/models")
def get_models(limit: int = Query(default=50, ge=1, le=500)):
    _ensure_lab_ready()
    active_program = _resolve_active_program()
    models = list_model_versions(limit=limit, program_id=(active_program.id if active_program else None))
    if not models:
        models = list_model_versions(limit=limit)
    return {"status": "ok", "models": [model.model_dump() for model in models], "total": len(models)}


@router.get("/segments")
def get_segments(
    model_version_id: str,
    include_labels: bool = Query(default=False),
    labels_limit: int = Query(default=2000, ge=1, le=20_000),
):
    _ensure_lab_ready()
    model = get_model_version(model_version_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Unknown model version: {model_version_id}")
    segments = get_regime_segments(model_version_id=model_version_id)
    payload: dict[str, object] = {
        "status": "ok",
        "model_version_id": model_version_id,
        "segments": [segment.model_dump() for segment in segments],
    }
    if include_labels:
        labels = get_regime_labels(model_version_id=model_version_id)
        payload["labels"] = [label.model_dump() for label in labels[-labels_limit:]]
    return payload


@router.post("/selector/decide", response_model=SelectorDecisionResponse)
def post_selector_decide(body: SelectorDecideRequest):
    _ensure_lab_ready()
    if body.model_version_id is None:
        resolved_model_id = _resolve_preferred_model_version_id(body.model_version_id, program_id=body.program_id)
        if resolved_model_id:
            body = body.model_copy(update={"model_version_id": resolved_model_id})
    try:
        return decide_current_regime(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/intents/dispatch-paper", response_model=DispatchPaperIntentResponse)
def post_intents_dispatch_paper(body: DispatchPaperIntentRequest):
    _ensure_lab_ready()
    try:
        return dispatch_paper_intent(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        log.exception("dispatch_paper_intent failed")
        raise HTTPException(status_code=500, detail=str(exc)) from None


@router.get("/reports/timeline")
def get_timeline_report(model_version_id: str):
    _ensure_lab_ready()
    model = get_model_version(model_version_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Unknown model version: {model_version_id}")
    segments = get_regime_segments(model_version_id=model_version_id)
    labels = get_regime_labels(model_version_id=model_version_id)
    model_config = dict(model.config_json or {})
    model_diagnostics = dict(model_config.get("diagnostics") or {})
    classifier = dict(model_config.get("classifier") or {})
    timeline_segments = [_timeline_segment_payload(segment) for segment in segments]
    timeline_labels = [_timeline_label_payload(label) for label in labels]
    timeline_price_points = _timeline_price_payloads(model=model, timeline_labels=timeline_labels)
    latest_label = timeline_labels[-1] if timeline_labels else None
    return {
        "status": "ok",
        "model_version_id": model_version_id,
        "taxonomy": list(REGIME_TAXONOMY),
        "timeframes": dict(model_config.get("timeframes") or {}),
        "validation": dict(model_config.get("validation") or {}),
        "diagnostics": model_diagnostics,
        "summary": {
            "segment_count": len(timeline_segments),
            "label_count": len(timeline_labels),
            "bars_classified": int(model_diagnostics.get("bars_classified") or len(timeline_labels)),
            "uncertain_share": float(model_diagnostics.get("uncertain_share") or model_diagnostics.get("transition_share") or 0.0),
            "raw_uncertain_share": float(
                model_diagnostics.get("raw_uncertain_share") or model_diagnostics.get("raw_transition_share") or 0.0
            ),
            "segment_median_bars": float(model_diagnostics.get("median_segment_bars") or 0.0),
            "classifier_type": str(classifier.get("type") or "").strip() or None,
            "current_regime": latest_label.get("display_regime") if latest_label else None,
            "current_core_regime": latest_label.get("core_regime") if latest_label else None,
            "current_uncertain": bool(latest_label.get("uncertain")) if latest_label else False,
        },
        "segments": timeline_segments,
        "labels": timeline_labels,
        "price_points": timeline_price_points,
    }


@router.get("/reports/heatmap")
def get_heatmap(model_version_id: str | None = Query(default=None)):
    _ensure_lab_ready()
    model_version_id = _resolve_preferred_model_version_id(model_version_id)
    if model_version_id is None:
        return {
            "status": "ok",
            "model_version_id": None,
            "taxonomy": list(REGIME_TAXONOMY),
            "regimes": [],
            "strategies": [],
            "cells": [],
            "diagnostics": {},
            "timeframes": {},
            "summary": {
                "total_cells": 0,
                "admitted_cells": 0,
                "scored_cells": 0,
                "rejected_cells": 0,
                "error_cells": 0,
                "insufficient_cells": 0,
                "legacy_cells": 0,
                "uncertain_share": 0.0,
                "raw_uncertain_share": 0.0,
                "bars_classified": 0,
                "segment_count": 0,
                "classifier_type": None,
            },
            "generated_at": None,
        }
    model = get_model_version(model_version_id)
    model_config = dict((model.config_json or {}) if model else {})
    timeframes = dict(model_config.get("timeframes") or {})
    model_diagnostics = dict(model_config.get("diagnostics") or {})
    classifier = dict(model_config.get("classifier") or {})
    rows = list_strategy_regime_scores(model_version_id)
    cells_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    strategy_sort: dict[str, tuple[int, float, int]] = {}
    summary = {
        "total_cells": 0,
        "admitted_cells": 0,
        "scored_cells": 0,
        "rejected_cells": 0,
        "error_cells": 0,
        "insufficient_cells": 0,
        "legacy_cells": 0,
        "uncertain_share": float(model_diagnostics.get("uncertain_share") or model_diagnostics.get("transition_share") or 0.0),
        "raw_uncertain_share": float(
            model_diagnostics.get("raw_uncertain_share") or model_diagnostics.get("raw_transition_share") or 0.0
        ),
        "bars_classified": int(model_diagnostics.get("bars_classified") or 0),
        "segment_count": int(model_diagnostics.get("segment_count") or 0),
        "classifier_type": str(classifier.get("type") or "").strip() or None,
    }
    for row in rows:
        metrics = dict(row.get("metrics_json") or {})
        raw_metrics = dict(metrics.get("raw") or {})
        adjusted_metrics = dict(metrics.get("adjusted") or {})
        oos_adjusted = dict(metrics.get("oos_adjusted") or {})
        row_diagnostics = dict(metrics.get("diagnostics") or {})
        admission = dict(row.get("admission_json") or {})
        strategy_meta = dict(metrics.get("strategy_meta") or {})
        state, primary_reason = _derive_heatmap_cell_state(
            score=float(row["score"] or 0.0),
            admission=admission,
            diagnostics=row_diagnostics,
        )
        strategy_id = str(strategy_meta.get("candidate_key") or row["strategy_id"])
        regime_payload = _normalized_report_regime(row.get("regime"))
        display_regime = str(regime_payload.get("display_regime") or row.get("regime") or "").strip()
        cell_payload = {
            "regime": display_regime,
            "core_regime": regime_payload.get("core_regime"),
            "stored_regime": regime_payload.get("stored_regime"),
            "legacy_regime": regime_payload.get("legacy_regime"),
            "strategy_id": strategy_id,
            "score": row["score"],
            "pre_cost_score": raw_metrics.get("total_return_pct"),
            "post_cost_score": adjusted_metrics.get("total_return_pct"),
            "oos_post_cost_score": oos_adjusted.get("total_return_pct"),
            "profit_factor": adjusted_metrics.get("profit_factor"),
            "sharpe": adjusted_metrics.get("sharpe"),
            "oos_profit_factor": oos_adjusted.get("profit_factor"),
            "admission": admission,
            "strategy_meta": strategy_meta,
            "state": state,
            "primary_reason": primary_reason,
            "diagnostics": row_diagnostics,
        }
        cell_key = (display_regime, strategy_id)
        previous = cells_by_key.get(cell_key)
        if previous is None or _heatmap_candidate_rank(cell_payload) < _heatmap_candidate_rank(previous):
            cells_by_key[cell_key] = cell_payload
        candidate_rank = _heatmap_candidate_rank(cell_payload)
        current_rank = strategy_sort.get(strategy_id)
        if current_rank is None or candidate_rank < current_rank:
            strategy_sort[strategy_id] = candidate_rank
    cells = sorted(cells_by_key.values(), key=lambda cell: (_regime_order(cell["regime"]), str(cell["strategy_id"])))
    for cell in cells:
        state = str(cell.get("state") or "")
        if state == "admitted":
            summary["admitted_cells"] += 1
        elif state == "scored":
            summary["scored_cells"] += 1
        elif state == "error":
            summary["error_cells"] += 1
        elif state == "insufficient_data":
            summary["insufficient_cells"] += 1
        else:
            summary["rejected_cells"] += 1
        if cell.get("legacy_regime"):
            summary["legacy_cells"] += 1
    summary["total_cells"] = len(cells)
    regimes = sorted({str(cell["regime"]) for cell in cells}, key=_regime_order)
    strategies = sorted(
        {str(cell["strategy_id"]) for cell in cells},
        key=lambda strategy_id: strategy_sort.get(strategy_id, (99, 0.0, 99)),
    )
    return {
        "status": "ok",
        "model_version_id": model_version_id,
        "taxonomy": list(REGIME_TAXONOMY),
        "timeframes": timeframes,
        "diagnostics": model_diagnostics,
        "regimes": regimes,
        "strategies": strategies,
        "cells": cells,
        "summary": summary,
        "generated_at": None if not rows else rows[0]["updated_at"],
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    _ensure_lab_ready()
    job = get_lab_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Lab job not found: {job_id}")
    return {"status": "ok", "job": job.model_dump(), "events": list_lab_job_events(job_id)}


@router.get("/jobs", response_model=QueueJobListResponse)
def get_jobs(
    state: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    _ensure_lab_ready()
    states: list[LabJobState] | None = None
    if state:
        states = []
        for raw in state:
            value = str(raw or "").strip().lower()
            try:
                states.append(LabJobState(value))
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Invalid lab job state: {raw}") from None
    jobs = list_lab_jobs(states=states, limit=limit)
    return QueueJobListResponse(jobs=jobs, total=len(jobs))


@router.get("/worker/health")
def get_worker_health():
    """Lightweight health check for polling (GlobalControlStrip, champion cards)."""
    _ensure_lab_ready()
    info = get_lab_worker_status()
    worker = info.get("worker") or {}
    return {
        "status": "ok",
        "active": info.get("active", False),
        "state": worker.get("state"),
        "current_job_id": worker.get("current_job_id"),
        "heartbeat_at": worker.get("heartbeat_at"),
        "heartbeat_age_seconds": worker.get("heartbeat_age_seconds"),
        "is_stale": worker.get("is_stale", True),
        "running_jobs_count": len(info.get("running_jobs") or []),
    }


@router.get("/worker/status")
def get_worker_status():
    _ensure_lab_ready()
    return {"status": "ok", **get_lab_worker_status()}


@router.get("/worker/feed")
def get_worker_feed(limit: int = Query(default=200, ge=10, le=2000)):
    _ensure_lab_ready()
    return {"status": "ok", **read_lab_worker_feed(limit_lines=limit)}


@router.post("/worker/start")
def post_worker_start():
    _ensure_lab_ready()
    try:
        result = start_lab_worker_process()
    except Exception as exc:
        log.exception("start_lab_worker_process failed")
        raise HTTPException(status_code=500, detail=f"Failed to start lab worker: {exc}") from None
    return {
        "status": "ok",
        "worker_status": result.get("status"),
        **{key: value for key, value in result.items() if key != "status"},
    }
