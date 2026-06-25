from __future__ import annotations

import json
from types import SimpleNamespace

from axiom.control_plane import ops as control_plane_ops
from axiom.db import get_db, kv_get, kv_set
from axiom.exchange.risk import is_trading_allowed


def test_stop_system_sets_pause_state_and_start_system_clears_it(AXIOM_db):
    stopped = control_plane_ops.stop_system()
    paused_state = kv_get("system_state", {})
    paused_legacy = kv_get("system_paused", False)
    paused_allowed, paused_reason = is_trading_allowed()

    started = control_plane_ops.start_system()
    resumed_state = kv_get("system_state", {})
    resumed_legacy = kv_get("system_paused", False)
    resumed_allowed, resumed_reason = is_trading_allowed()

    assert stopped == {"ok": True, "paused": True}
    assert paused_state["paused"] is True
    assert paused_state["paused_at"]
    assert paused_legacy is True
    assert paused_allowed is False
    assert paused_reason == "System paused by operator"
    assert started == {"ok": True, "paused": False}
    assert resumed_state["paused"] is False
    assert resumed_state["paused_at"] is None
    assert resumed_legacy is False
    assert resumed_allowed is True
    assert resumed_reason == "OK"


def test_pause_strategy_generation_does_not_toggle_trading_pause(AXIOM_db):
    # A fresh DB resolves to manual mode, where resume-generation deliberately
    # refuses (B-28: no silent manual->auto escalation; covered in
    # test_system_mode_semi). This test is about generation pause not touching
    # the TRADING pause, so run it in an explicit autonomous mode.
    from axiom.system_pause import set_system_mode

    set_system_mode("auto")

    paused = control_plane_ops.pause_strategy_generation()
    state_after_pause = kv_get("system_state", {})
    allowed_after_pause, reason_after_pause = is_trading_allowed()

    resumed = control_plane_ops.resume_strategy_generation()
    state_after_resume = kv_get("system_state", {})
    allowed_after_resume, reason_after_resume = is_trading_allowed()

    assert paused["ok"] is True
    assert paused["generation_paused"] is True
    assert paused["generation_paused_at"]
    assert state_after_pause["generation_paused"] is True
    assert state_after_pause["generation_paused_at"]
    assert state_after_pause.get("paused", False) is False
    assert allowed_after_pause is True
    assert reason_after_pause == "OK"

    assert resumed["ok"] is True
    assert resumed["generation_paused"] is False
    assert resumed["generation_paused_at"] is None
    assert state_after_resume["generation_paused"] is False
    assert state_after_resume["generation_paused_at"] is None
    assert state_after_resume.get("paused", False) is False
    assert allowed_after_resume is True
    assert reason_after_resume == "OK"


def test_trading_halt_reset_clears_pause_and_risk_gates(AXIOM_db):
    kv_set(
        "system_state",
        {
            "paused": True,
            "paused_at": "2026-03-12T09:00:00+00:00",
        },
    )
    kv_set("system_paused", True)
    kv_set(
        "risk_state",
        {
            "high_water_mark": 10000.0,
            "last_equity": 9200.0,
            "kill_switch_active": True,
            "kill_switch_triggered_at": "2026-03-12T09:05:00+00:00",
            "daily_loss_halt": True,
            "daily_loss_halt_date": "2026-03-12",
        },
    )

    payload = control_plane_ops.post_trading_halt_reset(SimpleNamespace(confirm=True))

    paused_state = kv_get("system_state", {})
    paused_legacy = kv_get("system_paused", False)
    risk_state = kv_get("risk_state", {})
    ops_state = kv_get("ops_manual_action_state", {})
    allowed, reason = is_trading_allowed()

    assert payload["ok"] is True
    assert payload["paused"] is False
    assert payload["trading_allowed"] is True
    assert payload["trading_reason"] == "OK"
    assert payload["reset"] == {
        "system_pause_cleared": True,
        "kill_switch_cleared": True,
        "daily_loss_halt_cleared": True,
    }
    assert paused_state["paused"] is False
    assert paused_state["paused_at"] is None
    assert paused_legacy is False
    assert risk_state["kill_switch_active"] is False
    assert risk_state["daily_loss_halt"] is False
    assert float(risk_state["high_water_mark"]) > 0
    assert payload["risk"]["high_water_mark"] == float(risk_state["high_water_mark"])
    assert allowed is True
    assert reason == "OK"
    assert ops_state["trading_reset"]["status"] == "ok"


def test_trading_halt_reset_reports_when_recovery_still_blocks_entries(AXIOM_db):
    kv_set(
        "system_state",
        {
            "paused": True,
            "paused_at": "2026-03-12T09:00:00+00:00",
        },
    )
    kv_set("system_paused", True)
    kv_set(
        "risk_state",
        {
            "high_water_mark": 10000.0,
            "last_equity": 9100.0,
            "kill_switch_active": True,
            "kill_switch_triggered_at": "2026-03-12T09:05:00+00:00",
            "daily_loss_halt": False,
            "daily_loss_halt_date": None,
        },
    )
    kv_set(
        "daemon_state",
        {
            "recovery_active": True,
            "recovery_status": "blocked",
            "recovery_summary": "Startup recovery blocked on 2 discrepancies.",
        },
    )

    payload = control_plane_ops.post_trading_halt_reset(SimpleNamespace(confirm=True))
    ops_state = kv_get("ops_manual_action_state", {})

    assert payload["ok"] is True
    assert payload["paused"] is False
    assert payload["trading_allowed"] is False
    assert "Startup exchange recovery active" in payload["trading_reason"]
    assert payload["reset"]["system_pause_cleared"] is True
    assert payload["reset"]["kill_switch_cleared"] is True
    assert ops_state["trading_reset"]["status"] == "warn"


def test_manual_exchange_reconcile_updates_daemon_and_operator_recovery_state(AXIOM_db, monkeypatch):
    kv_set(
        "daemon_state",
        {
            "running": True,
            "recovery_active": True,
            "recovery_status": "blocked",
            "recovery_summary": "Startup recovery blocked on 1 discrepancy.",
            "recovery_network": "testnet",
            "recovery_discrepancy_count": 1,
            "recovery_requires_operator": True,
        },
    )

    sync_calls: list[str] = []
    reconcile_calls: list[dict[str, object]] = []

    monkeypatch.setattr(control_plane_ops, "uuid4", lambda: SimpleNamespace(hex="abc123def4567890"))
    monkeypatch.setattr("axiom.exchange.risk.sync_from_trades", lambda: sync_calls.append("sync") or 1)

    def _fake_reconcile_all_books(testnet=True, **kwargs):
        reconcile_calls.append(dict(kwargs))
        return {
            "sqlite_open": 1,
            "exchange_open": 1,
            "synced": True,
            "discrepancies": [],
            "adopted_count": 1,
            "adopted_positions": [{"trade_id": "E9001", "asset": "BTC"}],
            "resolved_actions": [],
        }

    monkeypatch.setattr("axiom.exchange.risk.reconcile_all_books", _fake_reconcile_all_books)

    payload = control_plane_ops._run_manual_exchange_reconcile()

    daemon_state = kv_get("daemon_state", {})
    ops_state = kv_get("ops_manual_action_state", {})

    assert payload["ok"] is True
    assert payload["adopted_count"] == 1
    assert payload["recovery_batch_id"] == "manual-abc123def456"
    assert len(sync_calls) == 2
    assert reconcile_calls == [
        {
            "adopt_missing_in_sqlite": True,
            "recovery_batch_id": "manual-abc123def456",
        }
    ]
    assert daemon_state["recovery_active"] is False
    assert daemon_state["recovery_status"] == "resolved"
    assert daemon_state["recovery_batch_id"] == "manual-abc123def456"
    assert daemon_state["reconciliation_issues"] == 0
    assert "resolved exchange recovery" in daemon_state["recovery_summary"]
    assert ops_state["exchange_recovery"]["status"] == "ok"
    assert "resolved exchange recovery" in ops_state["exchange_recovery"]["summary"]
    assert ops_state["exchange_reconcile"]["details"]["adopted_count"] == 1


def test_recovery_batch_rollback_pauses_and_rebuilds_positions(AXIOM_db):
    kv_set(
        "daemon_state",
        {
            "running": True,
            "recovery_active": False,
            "recovery_status": "ok",
            "recovery_summary": "Exchange state is aligned.",
            "recovery_network": "testnet",
            "recovery_batch_id": "batch-rollback-1",
        },
    )

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO trades
            (
                id, display_id, strategy, strategy_name, strategy_id, asset, symbol,
                direction, entry_price, signal_entry_price, fill_entry_price, size,
                risk_pct, leverage, status, execution_type, timeframe, source, signal_data, opened_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?)
            """,
            (
                "E9001",
                "E9001",
                "exchange_recovered",
                "Exchange Recovered",
                "exchange_recovered",
                "BTC",
                "BTC",
                "long",
                68614.0,
                68614.0,
                68614.0,
                0.00471,
                0.01,
                20.0,
                "paper_challenger",
                "1h",
                "exchange_recovered",
                json.dumps(
                    {
                        "recovery_batch_id": "batch-rollback-1",
                        "recovered_from_trade_id": "E0095",
                    }
                ),
                "2026-03-12T08:35:04+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO trades
            (
                id, display_id, strategy, strategy_name, strategy_id, asset, symbol,
                direction, entry_price, signal_entry_price, fill_entry_price, size,
                risk_pct, leverage, status, execution_type, timeframe, source, signal_data, opened_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?)
            """,
            (
                "E1001",
                "E1001",
                "S01001",
                "Scanner BTC",
                "S01001",
                "ETH",
                "ETH",
                "long",
                2200.0,
                2200.0,
                2200.0,
                0.5,
                0.01,
                5.0,
                "paper_challenger",
                "1h",
                "scanner",
                json.dumps({}),
                "2026-03-12T09:05:04+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO portfolio_positions
            (trade_id, asset, direction, strategy, strategy_id, risk_pct, entry_price, correlation_group, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("E9001", "BTC", "long", "exchange_recovered", "exchange_recovered", 0.01, 68614.0, "crypto_beta", "2026-03-12T08:35:04+00:00"),
        )
        conn.execute(
            """
            INSERT INTO portfolio_positions
            (trade_id, asset, direction, strategy, strategy_id, risk_pct, entry_price, correlation_group, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("E1001", "ETH", "long", "S01001", "S01001", 0.01, 2200.0, "crypto_beta", "2026-03-12T09:05:04+00:00"),
        )

    payload = control_plane_ops._run_recovery_batch_rollback("batch-rollback-1")

    daemon_state = kv_get("daemon_state", {})
    ops_state = kv_get("ops_manual_action_state", {})
    paused_state = kv_get("system_state", {})
    allowed, reason = is_trading_allowed()

    with get_db() as conn:
        rolled_back_trade = conn.execute(
            "SELECT * FROM trades WHERE source = 'exchange_recovered' AND status = 'OPEN'"
        ).fetchone()
        remaining_trade = conn.execute(
            "SELECT * FROM trades WHERE source = 'scanner' AND status = 'OPEN' AND asset = 'ETH'"
        ).fetchone()
        rolled_back_position = conn.execute(
            "SELECT * FROM portfolio_positions WHERE asset = 'BTC'"
        ).fetchone()
        remaining_position = conn.execute(
            "SELECT * FROM portfolio_positions WHERE asset = 'ETH'"
        ).fetchone()

    assert payload["ok"] is True
    assert payload["paused"] is True
    assert payload["rolled_back_count"] == 1
    assert len(payload["rolled_back_trade_ids"]) == 1
    assert payload["rolled_back_trades"][0]["asset"] == "BTC"
    assert payload["rolled_back_trades"][0]["matched_trade_id"] == "E0095"
    assert paused_state["paused"] is True
    assert rolled_back_trade is None
    assert remaining_trade is not None
    assert rolled_back_position is None
    assert remaining_position is not None
    assert daemon_state["recovery_active"] is True
    assert daemon_state["recovery_status"] == "rolled_back"
    assert daemon_state["recovery_batch_id"] is None
    assert daemon_state["reconciliation_issues"] == 1
    assert "rerun exchange recovery before resuming entries" in daemon_state["recovery_summary"]
    assert ops_state["exchange_recovery"]["status"] == "warn"
    assert "rolled back" in ops_state["exchange_recovery"]["summary"]
    assert ops_state["recovery_rollback"]["details"]["rolled_back_count"] == 1
    assert allowed is False
    assert reason == "System paused by operator"
