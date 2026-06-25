"""Test that the dashboard response exposes circuit breaker states."""

from __future__ import annotations

from axiom.circuit_breaker import State
from axiom.control_plane import status as control_plane_status


def _stub_dashboard_deps(monkeypatch, *, breaker_states=None):
    """Stub the heavy dependencies of get_dashboard so it can run in tests."""
    if breaker_states is None:
        breaker_states = {"price": State.CLOSED, "trade": State.CLOSED, "account": State.CLOSED}

    monkeypatch.setattr(
        control_plane_status,
        "normalize_daemon_state",
        lambda write_back=True: {
            "running": True,
            "scan_count": 0,
            "last_prices": {},
            "account_equity": 1000.0,
            "exchange_account": {
                "accountValue": 1000.0,
                "totalMarginUsed": 0.0,
                "withdrawable": 1000.0,
                "network": "testnet",
                "source": "exchange",
                "synced_at": "2026-03-13T00:00:00+00:00",
            },
        },
    )
    monkeypatch.setattr(
        control_plane_status,
        "_extract_runtime_code_payload",
        lambda daemon: {},
    )
    monkeypatch.setattr(
        control_plane_status,
        "kv_get",
        lambda key, default=None: {
            "risk_state": {"high_water_mark": 1000.0, "drawdown_pct": 0.0},
            "daily_risk": {"current_equity": 1000.0, "start_equity": 1000.0},
            "sentiment": {},
            "simulation_state": {"active": False, "phase": "idle", "progress": 0, "prices": {}},
        }.get(key, default),
    )
    monkeypatch.setattr(control_plane_status, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(control_plane_status, "is_trading_allowed", lambda: (True, "OK"))
    monkeypatch.setattr(
        control_plane_status, "get_system_pause_state", lambda: {"paused": False},
    )
    monkeypatch.setattr(
        control_plane_status.core, "_load_settings_payload",
        lambda: {"exchange": "hyperliquid", "initial_capital": 1000.0},
    )

    # Patch the breaker module-level instances
    import axiom.circuit_breaker as cb_mod
    for attr, key in [("hl_price_breaker", "price"), ("hl_trade_breaker", "trade"), ("hl_account_breaker", "account")]:
        breaker = getattr(cb_mod, attr)
        monkeypatch.setattr(breaker, "state", breaker_states[key])


def test_dashboard_includes_circuit_breakers_all_closed(AXIOM_db, monkeypatch):
    _stub_dashboard_deps(monkeypatch)

    payload = control_plane_status.get_dashboard()

    assert "circuit_breakers" in payload
    assert payload["circuit_breakers"] == {
        "hl_price": "closed",
        "hl_trade": "closed",
        "hl_account": "closed",
    }


def test_dashboard_circuit_breakers_reflect_open_state(AXIOM_db, monkeypatch):
    _stub_dashboard_deps(monkeypatch, breaker_states={
        "price": State.OPEN,
        "trade": State.HALF_OPEN,
        "account": State.CLOSED,
    })

    payload = control_plane_status.get_dashboard()

    assert payload["circuit_breakers"]["hl_price"] == "open"
    assert payload["circuit_breakers"]["hl_trade"] == "half_open"
    assert payload["circuit_breakers"]["hl_account"] == "closed"


def test_dashboard_circuit_breakers_all_open(AXIOM_db, monkeypatch):
    _stub_dashboard_deps(monkeypatch, breaker_states={
        "price": State.OPEN,
        "trade": State.OPEN,
        "account": State.OPEN,
    })

    payload = control_plane_status.get_dashboard()

    assert payload["circuit_breakers"] == {
        "hl_price": "open",
        "hl_trade": "open",
        "hl_account": "open",
    }
