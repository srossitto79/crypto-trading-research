from __future__ import annotations

import json
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone

from axiom import api_core
from axiom.db import get_db


def _insert_strategy(strategy_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, stage_changed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                strategy_id,
                "rsi_momentum",
                "BTC",
                "1h",
                "{}",
                json.dumps({"sharpe": 1.2}),
                "gauntlet",
                "simulation-agent",
                "gauntlet",
                now,
                now,
                now,
            ),
        )


def test_coalesce_ws_messages_batches_multiple_payloads():
    payload = api_core._coalesce_ws_messages(
        [{"type": "logs", "entries": []}, {"type": "risk_alert", "data": {"kind": "kill_switch"}}]
    )

    assert payload == {
        "type": "batch",
        "messages": [
            {"type": "logs", "entries": []},
            {"type": "risk_alert", "data": {"kind": "kill_switch"}},
        ],
    }


def test_post_optimization_submit_uses_executor(monkeypatch, AXIOM_db):
    _insert_strategy("S30001")
    submitted: list[object] = []

    class _FakeExecutor:
        def submit(self, fn):
            submitted.append(fn)
            return object()

    monkeypatch.setattr(api_core, "_OPTIMIZATION_EXECUTOR", _FakeExecutor())

    result = api_core.post_optimization_submit(api_core.OptimizationSubmitBody(strategy_id="S30001"))

    assert result["status"] == "running"
    assert result["job_id"].startswith("opt_")
    assert len(submitted) == 1


def test_post_optimization_submit_persists_named_failure_details(monkeypatch, AXIOM_db):
    _insert_strategy("S30002")

    class _ImmediateExecutor:
        def submit(self, fn):
            fn()
            return object()

    def _raise_timeout(*_args, **_kwargs):
        raise FuturesTimeoutError()

    monkeypatch.setattr(api_core, "_OPTIMIZATION_EXECUTOR", _ImmediateExecutor())
    monkeypatch.setattr("axiom.strategies.optimizer.optimize_strategy", _raise_timeout)

    result = api_core.post_optimization_submit(
        api_core.OptimizationSubmitBody(
            strategy_id="S30002",
            n_trials=100,
            objective="sharpe_ratio",
        )
    )

    with get_db() as conn:
        row = conn.execute(
            "SELECT metrics_json, config_json FROM backtest_results WHERE result_id = ?",
            (result["result_id"],),
        ).fetchone()

    assert row is not None
    metrics = json.loads(row["metrics_json"] or "{}")
    config = json.loads(row["config_json"] or "{}")
    assert metrics["status"] == "failed"
    assert "timed out" in metrics["error"].lower()
    assert metrics["n_trials"] == 100
    assert config["status"] == "failed"
    assert "timed out" in config["error"].lower()
    assert config["n_trials"] == 100
    assert config["objective"] == "sharpe_ratio"


def test_get_backtest_result_preserves_failed_optimization_status(AXIOM_db):
    _insert_strategy("S30003")
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO backtest_results
            (result_id, strategy_id, result_type, symbol, timeframe, start_date, end_date, metrics_json, config_json, created_at)
            VALUES (?, ?, 'optimization', 'BTC', '1d', ?, ?, ?, ?, ?)
            """,
            (
                "opt-failed-30003",
                "S30003",
                "2022-03-14T00:00:00Z",
                "2026-03-13T00:00:00Z",
                json.dumps({"status": "failed", "error": "Grid search timed out after 300s"}),
                json.dumps({"status": "failed", "error": "Grid search timed out after 300s", "job_id": "opt_job_30003", "n_trials": 100}),
                now,
            ),
        )

    detail = api_core.get_backtest_result("opt-failed-30003", remote_skip=True)

    assert detail["id"] == "opt-failed-30003"
    assert detail["result_id"] == "opt-failed-30003"
    assert detail["job_id"] == "opt_job_30003"
    assert detail["status"] == "failed"
    assert detail["error"] == "Grid search timed out after 300s"
    assert detail["metrics"]["status"] == "failed"
    assert detail["metrics"]["error"] == "Grid search timed out after 300s"
    assert detail["metrics"]["n_trials"] == 100
    assert detail["config"]["status"] == "failed"
    assert detail["config"]["job_id"] == "opt_job_30003"
