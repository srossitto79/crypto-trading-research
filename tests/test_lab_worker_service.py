from __future__ import annotations

import time
from uuid import uuid4

from axiom.lab_db import (
    claim_next_lab_job,
    create_lab_experiment,
    create_or_update_model_version,
    enqueue_lab_job,
    get_lab_job,
    get_regime_program,
    list_lab_jobs,
    set_lab_meta,
    upsert_regime_program,
)
from axiom.lab_orchestrator import ORCHESTRATOR_JOB_TYPE, get_orchestrator_status, update_orchestrator_config
from axiom.lab_regime_engine import MODEL_REBUILD_JOB_TYPE, SEGMENT_BUILD_JOB_TYPE
from axiom.lab_models import LabJobState
from axiom.lab_worker_service import (
    WORKER_STATUS_META_KEY,
    _start_non_matrix_job_heartbeat,
    get_lab_worker_status,
    process_claimed_lab_job,
)


def test_process_claimed_lab_job_supports_model_rebuild(monkeypatch):
    experiment = create_lab_experiment(
        experiment_id=f"exp_{uuid4().hex[:8]}",
        symbol="BTC/USDT",
        timeframe="1h",
        status="ready",
    )
    job = enqueue_lab_job(
        job_type=MODEL_REBUILD_JOB_TYPE,
        experiment_id=experiment.id,
        payload={"experiment_id": experiment.id, "notes": "queued rebuild"},
    )
    claimed = claim_next_lab_job(worker_id="test-worker", job_type=MODEL_REBUILD_JOB_TYPE)
    assert claimed is not None

    monkeypatch.setattr(
        "axiom.lab_worker_service.run_model_rebuild_job",
        lambda payload: {
            "status": "ok",
            "experiment_id": str(payload["experiment_id"]),
            "model_version_id": "mv_queued",
            "labels_persisted": 123,
            "snapshot_path": "snapshot.parquet",
            "snapshot_hash": "hash123",
        },
    )

    summary = process_claimed_lab_job(worker_id="test-worker", job_id=job.id)

    assert summary["status"] == "ok"
    updated = get_lab_job(job.id)
    assert updated is not None
    assert updated.state.value == "succeeded"
    assert updated.progress_json["model_version_id"] == "mv_queued"


def test_process_claimed_lab_job_initializes_program_baseline(monkeypatch):
    program = upsert_regime_program(
        program_id="lrp_init_test",
        symbol="BTC/USDT",
        regime_timeframe="1h",
        execution_timeframe="15m",
        status="active",
    )
    experiment = create_lab_experiment(
        experiment_id=f"exp_{uuid4().hex[:8]}",
        program_id=program.id,
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        status="ready",
    )
    model = create_or_update_model_version(
        version_key=f"mv_init_{uuid4().hex[:8]}",
        program_id=program.id,
        experiment_id=experiment.id,
        status="active",
    )
    job = enqueue_lab_job(
        job_type=MODEL_REBUILD_JOB_TYPE,
        program_id=program.id,
        experiment_id=experiment.id,
        payload={
            "program_id": program.id,
            "experiment_id": experiment.id,
            "program_initialize": True,
            "auto_enqueue_segments": True,
            "min_segment_bars": 24,
        },
    )
    claimed = claim_next_lab_job(worker_id="test-worker", job_type=MODEL_REBUILD_JOB_TYPE)
    assert claimed is not None

    monkeypatch.setattr(
        "axiom.lab_worker_service.run_model_rebuild_job",
        lambda payload: {
            "status": "ok",
            "experiment_id": str(payload["experiment_id"]),
            "model_version_id": model.id,
            "labels_persisted": 123,
            "snapshot_path": "snapshot.parquet",
            "snapshot_hash": "hash123",
        },
    )

    summary = process_claimed_lab_job(worker_id="test-worker", job_id=job.id)

    assert summary["model_version_id"] == model.id
    refreshed_program = get_regime_program(program.id)
    assert refreshed_program is not None
    assert refreshed_program.active_model_version_id == model.id
    assert refreshed_program.status == "running"

    chained_segment_job = next(
        (
            row
            for row in list_lab_jobs(states=[LabJobState.QUEUED], limit=20)
            if row.program_id == program.id
            and row.job_type == SEGMENT_BUILD_JOB_TYPE
            and bool((row.payload_json or {}).get("program_initialize"))
        ),
        None,
    )
    assert chained_segment_job is not None


def test_process_claimed_lab_job_supports_segment_build(monkeypatch):
    job = enqueue_lab_job(
        job_type=SEGMENT_BUILD_JOB_TYPE,
        payload={"model_version_id": "mv_segments", "min_segment_bars": 24},
    )
    claimed = claim_next_lab_job(worker_id="test-worker", job_type=SEGMENT_BUILD_JOB_TYPE)
    assert claimed is not None

    monkeypatch.setattr(
        "axiom.lab_worker_service.run_segment_build_job",
        lambda payload: {
            "status": "ok",
            "model_version_id": str(payload["model_version_id"]),
            "segments_persisted": 15,
        },
    )

    summary = process_claimed_lab_job(worker_id="test-worker", job_id=job.id)

    assert summary["status"] == "ok"
    updated = get_lab_job(job.id)
    assert updated is not None
    assert updated.state.value == "succeeded"
    assert updated.progress_json["segments_persisted"] == 15


def test_non_matrix_job_heartbeat_loop_refreshes_claim(monkeypatch):
    beats: list[dict] = []

    monkeypatch.setattr(
        "axiom.lab_worker_service.heartbeat_lab_job",
        lambda *args, **kwargs: beats.append(dict(kwargs)),
    )
    monkeypatch.setattr("axiom.lab_worker_service._write_worker_status", lambda **_kwargs: None)

    stop_event, thread = _start_non_matrix_job_heartbeat(
        worker_id="test-worker",
        job_id="job-1",
        job_type="model_rebuild",
        lease_seconds=90,
        interval_seconds=0.1,
    )
    time.sleep(0.25)
    stop_event.set()
    thread.join(timeout=1.0)

    assert beats
    assert beats[0]["progress_json"]["phase"] == "working"
    assert beats[0]["progress_json"]["job_type"] == "model_rebuild"


def test_get_lab_worker_status_marks_stale_worker_inactive():
    set_lab_meta(
        WORKER_STATUS_META_KEY,
        {
            "worker_id": "stale-worker",
            "pid": 999,
            "hostname": "test-host",
            "state": "running",
            "current_job_id": "job-1",
            "heartbeat_at": time.time() - 600,
        },
    )

    status = get_lab_worker_status()

    assert status["active"] is False
    assert status["worker"]["is_stale"] is True


def test_process_claimed_lab_job_chains_continuous_cycle(monkeypatch):
    config = update_orchestrator_config(
        {
            "enabled": True,
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
            "strategy_sources": ["active", "graveyard"],
            "max_strategies": 8,
            "reserve_count": 3,
        }
    )

    cycle_job = enqueue_lab_job(
        job_type=ORCHESTRATOR_JOB_TYPE,
        payload={"cycle_id": "lcy_chain", "reason": "test", "config": config},
    )
    claimed_cycle = claim_next_lab_job(worker_id="test-worker", job_type=ORCHESTRATOR_JOB_TYPE)
    assert claimed_cycle is not None

    cycle_summary = process_claimed_lab_job(worker_id="test-worker", job_id=cycle_job.id)
    assert cycle_summary["status"] == "ok"

    status = get_orchestrator_status()
    model_job_id = status["pending_model_job_id"]
    assert status["state"] == "queued_model_rebuild"
    assert model_job_id

    claimed_model = claim_next_lab_job(worker_id="test-worker", job_type=MODEL_REBUILD_JOB_TYPE)
    assert claimed_model is not None
    assert claimed_model.id == model_job_id

    monkeypatch.setattr(
        "axiom.lab_worker_service.run_model_rebuild_job",
        lambda payload: {
            "status": "ok",
            "experiment_id": str(payload["experiment_id"]),
            "model_version_id": "mv_chain",
            "labels_persisted": 42,
            "snapshot_path": "snapshot.parquet",
            "snapshot_hash": "hash_chain",
        },
    )
    process_claimed_lab_job(worker_id="test-worker", job_id=claimed_model.id)

    status = get_orchestrator_status()
    segment_job_id = status["pending_segments_job_id"]
    assert status["state"] == "queued_segments"
    assert segment_job_id

    claimed_segment = claim_next_lab_job(worker_id="test-worker", job_type=SEGMENT_BUILD_JOB_TYPE)
    assert claimed_segment is not None
    assert claimed_segment.id == segment_job_id

    monkeypatch.setattr(
        "axiom.lab_worker_service.run_segment_build_job",
        lambda payload: {
            "status": "ok",
            "model_version_id": str(payload["model_version_id"]),
            "segments_persisted": 9,
        },
    )
    process_claimed_lab_job(worker_id="test-worker", job_id=claimed_segment.id)

    status = get_orchestrator_status()
    matrix_job_id = status["pending_matrix_job_id"]
    assert status["state"] == "queued_matrix"
    assert matrix_job_id

    matrix_job = get_lab_job(matrix_job_id)
    assert matrix_job is not None
    assert matrix_job.payload_json["strategy_sources"] == ["active", "graveyard"]
    assert matrix_job.payload_json["reserve_count"] == 3
