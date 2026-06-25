"""Risk math checks for drawdown and high-water mark tracking."""

from axiom.db import kv_get
from axiom.exchange.risk import update_equity


def test_drawdown_percent_tracks_high_water_mark(AXIOM_db):
    first = update_equity(10000.0)
    assert first["high_water_mark"] == 10000.0
    assert first["drawdown_pct"] == 0.0

    second = update_equity(9700.0)
    assert second["high_water_mark"] == 10000.0
    assert second["drawdown_pct"] == 0.03
    assert second["daily_pnl_pct"] == -0.03
    assert second["action"] is None

    third = update_equity(10200.0)
    assert third["high_water_mark"] == 10200.0
    assert third["drawdown_pct"] == 0.0

    fourth = update_equity(9690.0)
    assert fourth["high_water_mark"] == 10200.0
    assert fourth["drawdown_pct"] == 0.05
    assert fourth["action"] is None


def test_kill_switch_triggers_exactly_at_drawdown_threshold(AXIOM_db):
    # Establish HWM at 10,000 then drop exactly 10%.
    update_equity(10000.0)
    result = update_equity(9000.0)
    assert result["drawdown_pct"] == 0.1
    assert result["kill_switch"] is True
    assert result["action"] == "kill_switch"


def test_daily_loss_halt_triggers_exactly_at_limit(AXIOM_db):
    # Daily start equity set on first call; second call lands exactly at -5%.
    update_equity(10000.0)
    result = update_equity(9500.0)
    assert result["daily_pnl_pct"] == -0.05
    assert result["daily_halt"] is True
    assert result["action"] == "daily_halt"


def test_update_equity_persists_drawdown_and_daily_snapshot(AXIOM_db):
    update_equity(10000.0)
    update_equity(9700.0)

    risk_state = kv_get("risk_state", {})
    daily_state = kv_get("daily_risk", {})

    assert risk_state.get("drawdown_pct") == 0.03
    assert risk_state.get("last_equity") == 9700.0
    assert daily_state.get("current_equity") == 9700.0
    assert daily_state.get("pnl_pct") == -0.03
    assert daily_state.get("loss_pct") == 0.03
