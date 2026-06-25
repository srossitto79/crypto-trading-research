from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import axiom.config as cfg
from axiom.lab_db import (
    assert_lab_write_connection,
    create_lab_experiment,
    enqueue_lab_job,
    get_lab_job,
    init_lab_db,
    list_lab_jobs,
)
from axiom.lab_models import LabJobState


def test_init_lab_db_creates_expected_tables():
    init_lab_db()

    assert cfg.AXIOM_LAB_DB.exists()

    with sqlite3.connect(str(cfg.AXIOM_LAB_DB)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    table_names = {str(row[0]) for row in rows}

    expected = {
        "lab_meta",
        "lab_regime_model_version",
        "lab_regime_labels",
        "lab_regime_segments",
        "lab_strategy_regime_scores",
        "lab_regime_container",
        "lab_regime_container_member",
        "lab_regime_champion",
        "lab_selection_event",
        "lab_signal_intent",
        "lab_execution_feedback",
        "lab_job_queue",
        "lab_job_event",
    }
    assert expected.issubset(table_names)

    with sqlite3.connect(str(cfg.AXIOM_LAB_DB)) as conn:
        columns = conn.execute("PRAGMA table_info(lab_experiment)").fetchall()
    experiment_columns = {str(row[1]) for row in columns}
    assert {"regime_timeframe", "execution_timeframe"}.issubset(experiment_columns)


def test_enqueue_and_fetch_lab_job():
    init_lab_db()
    job = enqueue_lab_job(
        job_type="experiment_create",
        payload={"symbol": "BTC/USDT", "timeframe": "1h"},
        experiment_id="exp_test",
    )

    assert job.state == LabJobState.QUEUED
    assert job.experiment_id == "exp_test"

    fetched = get_lab_job(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.state == LabJobState.QUEUED

    listed = list_lab_jobs(states=[LabJobState.QUEUED], limit=20)
    assert any(row.id == job.id for row in listed)


def test_lab_write_guard_rejects_non_lab_connection(AXIOM_db):
    with sqlite3.connect(str(cfg.AXIOM_DB)) as conn:
        try:
            assert_lab_write_connection(conn)
        except RuntimeError as exc:
            assert "Lab write guard blocked connection" in str(exc)
        else:
            raise AssertionError("Expected lab write guard to reject production DB connection")


def test_lab_db_module_does_not_use_production_get_db():
    source = Path("Axiom/lab_db.py").read_text(encoding="utf-8")
    assert "from axiom.db import get_db" not in source
    assert "axiom.db.get_db" not in source


def test_create_lab_experiment_supports_separate_timeframes():
    init_lab_db()
    experiment = create_lab_experiment(
        experiment_id=f"exp_dual_tf_{uuid4().hex[:8]}",
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        status="ready",
    )

    assert experiment.timeframe == "1h"
    assert experiment.regime_timeframe == "1h"
    assert experiment.execution_timeframe == "15m"
