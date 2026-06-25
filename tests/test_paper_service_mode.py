"""Paper service high-activity mode safety/restore tests."""

from __future__ import annotations

from axiom.db import get_db, kv_get
from axiom.api_domains import paper as paper_domain
from axiom.control_plane import ops as control_plane_ops


def test_start_paper_service_high_activity_enables_test_flags(AXIOM_db, monkeypatch):
    import axiom.api_core as core

    monkeypatch.setattr("axiom.scheduler.apply_runtime_scheduler_overrides", lambda: 0)
    monkeypatch.setattr("axiom.scanner.run_scan", lambda execute_positions=True: {})

    control_plane_ops.get_scheduler()
    result = paper_domain.start_paper_service(high_activity_test=True, run_scan_now=False)
    settings = core.get_settings()
    state = kv_get("paper_service_state", {})

    assert result["status"] == "running"
    assert result["running"] is True
    assert result["high_activity_test"] is True
    assert settings["paper_test_mode_enabled"] is True
    assert settings["paper_test_high_activity_enabled"] is True
    assert settings["paper_test_bypass_gates_enabled"] is True
    assert settings["relaxed_trade_filters_enabled"] is True
    assert settings["strict_regime_gating"] is False
    assert int(settings["scanner_signal_interval_minutes"]) == 1
    assert int(settings["scanner_execution_interval_minutes"]) == 1
    assert state.get("running") is True
    assert state.get("high_activity_test") is True

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, enabled FROM scheduler_jobs WHERE id IN ('Axiom-scanner-signal', 'Axiom-scanner-hourly')"
        ).fetchall()
    by_id = {row["id"]: int(row["enabled"]) for row in rows}
    assert by_id.get("Axiom-scanner-signal") == 1
    assert by_id.get("Axiom-scanner-hourly") == 1


def test_stop_paper_service_restores_settings_and_disables_jobs(AXIOM_db, monkeypatch):
    import axiom.api_core as core

    monkeypatch.setattr("axiom.scheduler.apply_runtime_scheduler_overrides", lambda: 0)
    monkeypatch.setattr("axiom.scanner.run_scan", lambda execute_positions=True: {})

    control_plane_ops.get_scheduler()
    baseline = core.get_settings()
    paper_domain.start_paper_service(high_activity_test=True, run_scan_now=False)

    result = paper_domain.stop_paper_service(disable_test_mode=True)
    settings = core.get_settings()
    state = kv_get("paper_service_state", {})

    assert result["status"] == "stopped"
    assert result["running"] is False
    assert result["high_activity_test"] is False
    assert state.get("running") is False
    assert state.get("high_activity_test") is False
    assert settings["paper_test_mode_enabled"] == baseline["paper_test_mode_enabled"]
    assert settings["paper_test_high_activity_enabled"] == baseline["paper_test_high_activity_enabled"]
    assert settings["paper_test_bypass_gates_enabled"] == baseline["paper_test_bypass_gates_enabled"]

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, enabled FROM scheduler_jobs WHERE id IN ('Axiom-scanner-signal', 'Axiom-scanner-hourly')"
        ).fetchall()
    by_id = {row["id"]: int(row["enabled"]) for row in rows}
    assert by_id.get("Axiom-scanner-signal") == 0
    assert by_id.get("Axiom-scanner-hourly") == 0
