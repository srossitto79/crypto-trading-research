"""Tests for Priority 1: Regime Lab completion — PID lock, feature flag, stale recovery, approval gate."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest


def test_pid_lock_acquire_and_release(tmp_path, monkeypatch):
    """PID file singleton prevents duplicate workers and cleans up on release."""
    monkeypatch.setattr("axiom.lab_worker_service.AXIOM_HOME", tmp_path)
    from axiom.lab_worker_service import acquire_pid_lock, release_pid_lock, _pid_file_path

    pid_path = _pid_file_path()

    # First acquire should succeed
    assert acquire_pid_lock() is True
    assert pid_path.exists()
    assert int(pid_path.read_text().strip()) == os.getpid()

    # Second acquire from same process should fail (PID is alive)
    assert acquire_pid_lock() is False

    # Release should clean up
    release_pid_lock()
    assert not pid_path.exists()


def test_pid_lock_stale_pid_reclaimed(tmp_path, monkeypatch):
    """A stale PID file (dead process) should be overwritten."""
    monkeypatch.setattr("axiom.lab_worker_service.AXIOM_HOME", tmp_path)
    from axiom.lab_worker_service import acquire_pid_lock, _pid_file_path

    pid_path = _pid_file_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    # Write a PID that is almost certainly not alive
    pid_path.write_text("999999999")

    assert acquire_pid_lock() is True
    assert int(pid_path.read_text().strip()) == os.getpid()


def test_pid_lock_reclaims_exited_windows_process_handle(tmp_path, monkeypatch):
    """Windows should reclaim stale PID files even if the old process object lingers."""
    monkeypatch.setattr("axiom.lab_worker_service.AXIOM_HOME", tmp_path)
    from axiom.lab_worker_service import acquire_pid_lock, _pid_file_path

    pid_path = _pid_file_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("227248")

    monkeypatch.setattr("axiom.lab_worker_service.os.name", "nt")
    monkeypatch.setattr("axiom.lab_worker_service.psutil.Process", lambda _pid: (_ for _ in ()).throw(__import__("psutil").NoSuchProcess(_pid)))

    assert acquire_pid_lock() is True
    assert int(pid_path.read_text().strip()) == os.getpid()


def test_feature_flag_regime_lab():
    """Feature flag reads AXIOM_ENABLE_REGIME_LAB correctly."""
    from axiom.lab_features import regime_lab_enabled

    original = os.environ.get("AXIOM_ENABLE_REGIME_LAB")
    try:
        os.environ["AXIOM_ENABLE_REGIME_LAB"] = "1"
        assert regime_lab_enabled() is True

        os.environ["AXIOM_ENABLE_REGIME_LAB"] = "true"
        assert regime_lab_enabled() is True

        os.environ["AXIOM_ENABLE_REGIME_LAB"] = "0"
        assert regime_lab_enabled() is False

        os.environ["AXIOM_ENABLE_REGIME_LAB"] = ""
        assert regime_lab_enabled() is False

        del os.environ["AXIOM_ENABLE_REGIME_LAB"]
        assert regime_lab_enabled() is False
    finally:
        if original is not None:
            os.environ["AXIOM_ENABLE_REGIME_LAB"] = original
        elif "AXIOM_ENABLE_REGIME_LAB" in os.environ:
            del os.environ["AXIOM_ENABLE_REGIME_LAB"]


def test_feature_flag_no_longer_falls_back_to_orchestrator_db(monkeypatch):
    """Unset env must keep Regime Lab dormant even if DB config says enabled."""
    from axiom.lab_db import set_lab_meta
    from axiom.lab_features import regime_lab_enabled
    from axiom.lab_orchestrator import ORCHESTRATOR_CONFIG_META_KEY

    original = os.environ.get("AXIOM_ENABLE_REGIME_LAB")
    try:
        if "AXIOM_ENABLE_REGIME_LAB" in os.environ:
            del os.environ["AXIOM_ENABLE_REGIME_LAB"]
        set_lab_meta(ORCHESTRATOR_CONFIG_META_KEY, {"enabled": True, "auto_start_worker": True})

        assert regime_lab_enabled() is False
    finally:
        if original is not None:
            os.environ["AXIOM_ENABLE_REGIME_LAB"] = original
        elif "AXIOM_ENABLE_REGIME_LAB" in os.environ:
            del os.environ["AXIOM_ENABLE_REGIME_LAB"]


def test_quiesce_regime_lab_disables_orchestrator_and_fails_active_jobs(monkeypatch):
    """Dormancy should pause orchestrator state and fail queued/running jobs."""
    from axiom.lab_db import (
        claim_next_lab_job,
        enqueue_lab_job,
        get_lab_job,
        get_lab_meta,
        set_lab_meta,
    )
    from axiom.lab_dormancy import quiesce_regime_lab
    from axiom.lab_models import LabJobState
    from axiom.lab_orchestrator import ORCHESTRATOR_CONFIG_META_KEY, ORCHESTRATOR_STATUS_META_KEY

    queued_job = enqueue_lab_job(job_type="continuous_cycle", payload={"program_id": "lrp_test"})
    running_job = enqueue_lab_job(job_type="model_rebuild", payload={"program_id": "lrp_test"})
    claimed = claim_next_lab_job(worker_id="test-worker", job_type="model_rebuild")
    assert claimed is not None
    assert claimed.id == running_job.id

    set_lab_meta(
        ORCHESTRATOR_CONFIG_META_KEY,
        {"enabled": True, "auto_start_worker": True, "program_id": "lrp_test"},
    )
    set_lab_meta(
        ORCHESTRATOR_STATUS_META_KEY,
        {
            "state": "queued_matrix",
            "program_id": "lrp_test",
            "pending_model_job_id": queued_job.id,
            "pending_segments_job_id": running_job.id,
            "pending_matrix_job_id": "ljq_unused",
            "next_run_at": "2026-03-21T12:00:00+00:00",
        },
    )
    set_lab_meta(
        "lab_worker_status",
        {
            "worker_id": "lab-worker:test",
            "pid": 999999,
            "state": "running",
            "current_job_id": running_job.id,
        },
    )

    summary = quiesce_regime_lab(reason="feature_dormant")

    config = get_lab_meta(ORCHESTRATOR_CONFIG_META_KEY, {})
    status = get_lab_meta(ORCHESTRATOR_STATUS_META_KEY, {})
    worker = get_lab_meta("lab_worker_status", {})
    refreshed_queued = get_lab_job(queued_job.id)
    refreshed_running = get_lab_job(running_job.id)

    assert summary["failed_jobs"] >= 2
    assert config["enabled"] is False
    assert config["auto_start_worker"] is False
    assert status["state"] == "paused"
    assert status["next_run_at"] is None
    assert status["pending_model_job_id"] is None
    assert status["pending_segments_job_id"] is None
    assert status["pending_matrix_job_id"] is None
    assert worker["state"] == "stopped"
    assert worker["current_job_id"] is None
    assert worker["last_error"] == "feature_dormant"
    assert refreshed_queued is not None
    assert refreshed_queued.state == LabJobState.FAILED
    assert refreshed_queued.error_json["error"] == "feature_dormant"
    assert refreshed_running is not None
    assert refreshed_running.state == LabJobState.FAILED
    assert refreshed_running.error_json["error"] == "feature_dormant"


def test_lab_worker_start_refuses_when_feature_is_dormant(monkeypatch):
    """Worker startup should refuse while Regime Lab is dormant."""
    monkeypatch.setenv("AXIOM_ENABLE_REGIME_LAB", "0")

    from axiom.lab_worker_service import start_lab_worker_process

    with pytest.raises(RuntimeError, match="Regime Lab is dormant"):
        start_lab_worker_process()


def test_recover_stale_lab_jobs():
    """Stale jobs (lease expired) are recovered back to queued."""
    from axiom.lab_db import (
        claim_next_lab_job,
        create_lab_experiment,
        enqueue_lab_job,
        get_lab_job,
        recover_stale_lab_jobs,
    )
    from axiom.lab_models import LabJobState
    from axiom.lab_regime_engine import MODEL_REBUILD_JOB_TYPE

    experiment = create_lab_experiment(
        experiment_id=f"exp_stale_{uuid4().hex[:8]}",
        symbol="BTC/USDT",
        timeframe="1h",
        status="ready",
    )
    enqueue_lab_job(
        job_type=MODEL_REBUILD_JOB_TYPE,
        experiment_id=experiment.id,
        payload={"experiment_id": experiment.id},
    )

    # Claim the job to put it in RUNNING state with minimum lease (5s enforced)
    claimed = claim_next_lab_job(
        worker_id="stale-test-worker",
        job_type=MODEL_REBUILD_JOB_TYPE,
        lease_seconds=5,
    )
    assert claimed is not None
    assert claimed.state == LabJobState.RUNNING

    # Wait for lease to expire (min lease is 5s)
    time.sleep(6)

    # Recovery should find the stale job (lease_expires_at < now)
    recovered_count = recover_stale_lab_jobs(worker_timeout_seconds=5)
    assert recovered_count >= 1

    # Job should be back to queued
    refreshed = get_lab_job(claimed.id)
    assert refreshed is not None
    assert refreshed.state == LabJobState.QUEUED


def test_detect_champion_changes_identifies_new_champion():
    """_detect_champion_changes detects when a champion has changed."""
    from axiom.lab_matrix_engine import _detect_champion_changes

    # No existing containers — any champion is a change
    changes = _detect_champion_changes(
        model_version_id="nonexistent_mv",
        container_payloads=[
            {
                "regime": "TREND_UP",
                "champion": {"strategy_id": "strat_new", "score": 0.85},
                "members": [],
            }
        ],
    )
    assert len(changes) == 1
    assert changes[0]["regime"] == "TREND_UP"
    assert changes[0]["new_champion_strategy_id"] == "strat_new"
    assert changes[0]["old_champion_strategy_id"] is None


def test_detect_champion_changes_no_change_when_same():
    """No changes reported when champion strategy_id is the same."""
    from axiom.lab_db import (
        create_lab_experiment,
        create_or_update_model_version,
        replace_regime_containers,
    )
    from axiom.lab_matrix_engine import _detect_champion_changes

    exp_id = f"exp_champ_{uuid4().hex[:8]}"
    create_lab_experiment(experiment_id=exp_id, symbol="BTC/USDT", timeframe="1h", status="ready")
    mv = create_or_update_model_version(
        experiment_id=exp_id,
        version_key=f"test_v1_{uuid4().hex[:6]}",
        config_json={"classifier": {"type": "legacy_rule"}},
    )
    mv_id = mv.id
    replace_regime_containers(
        model_version_id=mv_id,
        score_version="v1",
        regimes=[
            {
                "regime": "RANGE",
                "champion": {"strategy_id": "strat_existing", "score": 0.7},
                "members": [{"strategy_id": "strat_existing", "rank": 1, "score": 0.7}],
                "meta_json": {},
            }
        ],
    )

    changes = _detect_champion_changes(
        model_version_id=mv_id,
        container_payloads=[
            {
                "regime": "RANGE",
                "champion": {"strategy_id": "strat_existing", "score": 0.75},
                "members": [{"strategy_id": "strat_existing", "rank": 1, "score": 0.75}],
            }
        ],
    )
    assert len(changes) == 0
