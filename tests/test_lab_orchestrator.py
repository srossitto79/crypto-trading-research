from __future__ import annotations

from datetime import UTC, datetime

from axiom.lab_db import get_lab_experiment, get_lab_job
from axiom.lab_db import replace_regime_segments, upsert_lab_experiment, upsert_regime_program, upsert_snapshot_manifest
from axiom.lab_models import LabJobState
from axiom.lab_orchestrator import (
    ORCHESTRATOR_JOB_TYPE,
    enqueue_continuous_cycle,
    get_orchestrator_status,
    maybe_enqueue_due_continuous_cycle,
    run_orchestrator_cycle_job,
    update_orchestrator_config,
)


def test_maybe_enqueue_due_continuous_cycle_honors_enabled_schedule(monkeypatch):
    fixed_now = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("axiom.lab_orchestrator._now", lambda: fixed_now)

    config = update_orchestrator_config(
        {
            "enabled": True,
            "cadence_hours": 8,
            "strategy_sources": ["graveyard", "graveyard", "active", "bogus"],
        }
    )
    assert config["strategy_sources"] == ["graveyard", "active"]

    queued = maybe_enqueue_due_continuous_cycle()
    assert queued is not None

    job = get_lab_job(str(queued["job_id"]))
    assert job is not None
    assert job.job_type == ORCHESTRATOR_JOB_TYPE
    assert job.state == LabJobState.QUEUED

    status = get_orchestrator_status()
    assert status["state"] == "queued"
    assert status["last_cycle_id"] == queued["cycle_id"]
    assert status["next_run_at"] is not None


def test_run_orchestrator_cycle_job_upserts_experiment_and_model_job(monkeypatch):
    fixed_now = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("axiom.lab_orchestrator._now", lambda: fixed_now)

    config = update_orchestrator_config(
        {
            "enabled": True,
            "symbol": "ETH/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
            "classifier_type": "gmm_v1",
            "classifier_config": {"n_components": 4, "transition_probability": 0.58},
            "max_strategies": 12,
            "strategy_sources": ["active", "graveyard"],
        }
    )
    assert config["classifier_type"] == "gmm_v1"
    assert config["classifier_config"]["n_components"] == 4

    summary = run_orchestrator_cycle_job(
        {
            "cycle_id": "lcy_test_001",
            "reason": "manual",
            "config": config,
        },
        job_id="ljq_orch_test",
    )

    assert summary["status"] == "ok"
    experiment = get_lab_experiment(summary["experiment_id"])
    assert experiment is not None
    assert experiment.symbol == "ETH/USDT"
    assert experiment.regime_timeframe == "1h"
    assert experiment.execution_timeframe == "15m"

    model_job = get_lab_job(summary["model_job_id"])
    assert model_job is not None
    assert model_job.job_type == "model_rebuild"
    assert model_job.payload_json["orchestrator"]["cycle_id"] == "lcy_test_001"
    assert model_job.payload_json["classifier_type"] == "gmm_v1"
    assert model_job.payload_json["classifier_config"]["n_components"] == 4
    assert model_job.payload_json["strategy_sources"] == ["active", "graveyard"]
    assert model_job.payload_json["reserve_count"] == config["reserve_count"]


def test_enqueue_continuous_cycle_blocks_when_chain_is_active(monkeypatch):
    fixed_now = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("axiom.lab_orchestrator._now", lambda: fixed_now)

    update_orchestrator_config({"enabled": True})
    first = enqueue_continuous_cycle(reason="manual", force=True)
    assert first is not None

    second = enqueue_continuous_cycle(reason="manual", force=True)
    assert second is None


def test_run_orchestrator_cycle_job_reuses_active_program_model(monkeypatch):
    fixed_now = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("axiom.lab_orchestrator._now", lambda: fixed_now)

    program = upsert_regime_program(
        program_id="lrp_test_reuse",
        symbol="BTC/USDT",
        regime_timeframe="1h",
        execution_timeframe="15m",
        status="active",
    )
    experiment = get_lab_experiment(
        run_orchestrator_cycle_job(
            {
                "cycle_id": "lcy_seed",
                "reason": "seed",
                "config": update_orchestrator_config(
                    {
                        "program_id": program.id,
                        "enabled": True,
                        "symbol": "BTC/USDT",
                        "regime_timeframe": "1h",
                        "execution_timeframe": "15m",
                        "refresh_classifier_each_cycle": True,
                    }
                ),
            },
            job_id="ljq_seed",
        )["experiment_id"]
    )
    assert experiment is not None

    from axiom.lab_db import create_or_update_model_version, update_regime_program as _update_program

    model = create_or_update_model_version(
        version_key="rm_reuse_baseline",
        program_id=program.id,
        experiment_id=experiment.id,
        status="active",
    )
    replace_regime_segments(
        model_version_id=model.id,
        symbol="BTC/USDT",
        timeframe="1h",
        segments=[
            {
                "regime": "TREND_UP_LOW_VOL",
                "segment_start": "2026-01-01T00:00:00+00:00",
                "segment_end": "2026-01-02T00:00:00+00:00",
                "confidence_avg": 0.8,
                "bars_count": 24,
                "meta_json": {},
            }
        ],
    )
    _update_program(program.id, active_experiment_id=experiment.id, active_model_version_id=model.id, status="active")

    config = update_orchestrator_config(
        {
            "program_id": program.id,
            "enabled": True,
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
            "max_strategies": 4,
            "strategy_sources": ["active", "graveyard"],
            "refresh_classifier_each_cycle": False,
        }
    )
    summary = run_orchestrator_cycle_job(
        {
            "cycle_id": "lcy_reuse_001",
            "reason": "manual",
            "config": config,
        },
        job_id="ljq_reuse_test",
    )

    assert summary["mode"] == "reuse_active_model"
    assert summary["model_version_id"] == model.id
    assert "matrix_job_id" in summary

    matrix_job = get_lab_job(summary["matrix_job_id"])
    assert matrix_job is not None
    assert matrix_job.job_type == "backtests_matrix"
    assert matrix_job.payload_json["model_version_id"] == model.id
    assert matrix_job.payload_json["program_id"] == program.id


def test_run_orchestrator_cycle_job_refreshes_when_baseline_window_is_too_short(monkeypatch):
    fixed_now = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("axiom.lab_orchestrator._now", lambda: fixed_now)

    program = upsert_regime_program(
        program_id="lrp_test_short_window",
        symbol="BTC/USDT",
        regime_timeframe="1h",
        execution_timeframe="15m",
        status="active",
    )
    short_experiment = upsert_lab_experiment(
        experiment_id="exp_short_window",
        program_id=program.id,
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        train_start="2025-03-18T12:00:00+00:00",
        train_end="2025-09-18T12:00:00+00:00",
        test_start="2025-09-18T12:00:01+00:00",
        test_end="2026-03-18T12:00:00+00:00",
        notes="Short baseline coverage",
        status="active",
    )
    upsert_snapshot_manifest(
        experiment_id=short_experiment.id,
        snapshot_path="C:/tmp/short-window.parquet",
        snapshot_hash="short-window-hash",
        symbol="BTC/USDT",
        timeframe="1h",
        row_count=1000,
        coverage_start="2025-03-18T12:00:00+00:00",
        coverage_end="2026-03-18T12:00:00+00:00",
        manifest_json={"requested_window_start": "2025-03-18T12:00:00+00:00", "requested_window_end": "2026-03-18T12:00:00+00:00"},
    )

    from axiom.lab_db import create_or_update_model_version, update_regime_program as _update_program

    model = create_or_update_model_version(
        version_key="rm_short_baseline",
        program_id=program.id,
        experiment_id=short_experiment.id,
        status="active",
    )
    replace_regime_segments(
        model_version_id=model.id,
        symbol="BTC/USDT",
        timeframe="1h",
        segments=[
            {
                "regime": "TRANSITION",
                "segment_start": "2025-09-18T13:00:00+00:00",
                "segment_end": "2025-09-19T13:00:00+00:00",
                "confidence_avg": 0.7,
                "bars_count": 24,
                "meta_json": {},
            }
        ],
    )
    _update_program(program.id, active_experiment_id=short_experiment.id, active_model_version_id=model.id, status="active")

    config = update_orchestrator_config(
        {
            "program_id": program.id,
            "enabled": True,
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
            "max_strategies": 4,
            "strategy_sources": ["active", "graveyard"],
            "refresh_classifier_each_cycle": False,
            "train_lookback_days": 365,
            "oos_lookback_days": 365,
        }
    )
    summary = run_orchestrator_cycle_job(
        {
            "cycle_id": "lcy_refresh_needed",
            "reason": "manual",
            "config": config,
        },
        job_id="ljq_refresh_needed",
    )

    assert summary["mode"] == "refresh_classifier"
    assert "model_job_id" in summary
    model_job = get_lab_job(summary["model_job_id"])
    assert model_job is not None
    assert model_job.job_type == "model_rebuild"
