"""Dashboard account-equity fallback behavior."""

from __future__ import annotations

import pytest

try:
    import hyperliquid  # noqa: F401
    _HAS_HYPERLIQUID = True
except ImportError:
    _HAS_HYPERLIQUID = False

_skip_no_hl = pytest.mark.skipif(not _HAS_HYPERLIQUID, reason="hyperliquid package not installed")


@_skip_no_hl
def test_get_dashboard_defaults_to_initial_capital_in_paper_mode(AXIOM_db, monkeypatch):
    from axiom.control_plane import status as control_plane_status

    kv_payloads = {
        "daemon_state": {},
        "risk_state": {},
        "daily_risk": {},
        "sentiment": {},
        "simulation_state": {"active": False, "phase": "idle"},
    }

    monkeypatch.setattr(control_plane_status, "kv_get", lambda key, default=None: kv_payloads.get(key, default))
    monkeypatch.setattr(control_plane_status, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(control_plane_status, "is_trading_allowed", lambda: (True, "OK"))
    monkeypatch.setattr(control_plane_status.core, "_load_settings_payload", lambda: {"initial_capital": 10000, "exchange": "hyperliquid"})
    monkeypatch.setattr(control_plane_status.core, "_resolve_exchange_testnet", lambda: True)

    def _raise_exchange_error(*_args, **_kwargs):
        raise RuntimeError("exchange unavailable")

    monkeypatch.setattr("axiom.exchange.hyperliquid.get_account_value", _raise_exchange_error)

    payload = control_plane_status.get_dashboard()
    assert payload["execution_mode"] == "paper"
    assert payload["simulation_active"] is False
    assert payload["account"]["accountValue"] == 10000.0


@_skip_no_hl
def test_get_dashboard_strict_raises_when_hyperliquid_unavailable(AXIOM_db, monkeypatch):
    from axiom.control_plane import status as control_plane_status

    kv_payloads = {
        "daemon_state": {"account_equity": 10000.0},
        "risk_state": {},
        "daily_risk": {"start_equity": 10000.0, "current_equity": 10000.0},
        "sentiment": {},
        "simulation_state": {"active": False, "phase": "idle"},
    }

    monkeypatch.setattr(control_plane_status, "kv_get", lambda key, default=None: kv_payloads.get(key, default))
    monkeypatch.setattr(control_plane_status, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(control_plane_status, "is_trading_allowed", lambda: (True, "OK"))
    monkeypatch.setattr(control_plane_status.core, "_load_settings_payload", lambda: {"initial_capital": 10000, "exchange": "hyperliquid"})
    monkeypatch.setattr(control_plane_status.core, "_resolve_exchange_testnet", lambda: True)

    def _raise_exchange_error(*_args, **_kwargs):
        raise RuntimeError("auth failed")

    monkeypatch.setattr("axiom.exchange.hyperliquid.get_account_value", _raise_exchange_error)

    with pytest.raises(control_plane_status.HTTPException) as exc:
        control_plane_status.get_dashboard(require_account_connection=True)

    assert exc.value.status_code == 503
    assert "Unable to fetch HyperLiquid wallet balance" in str(exc.value.detail)
