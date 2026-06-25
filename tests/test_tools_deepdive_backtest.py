import json
import pytest

from axiom.db import get_db, init_db


def _seed_strategy(sid="S77001", asset="BTC", tf="1h", stype="rsi", params=None):
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategies (id, name, type, symbol, timeframe, params) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "t", stype, asset, tf, json.dumps(params or {"rsi_period": 14})),
        )
        conn.commit()
    return sid


def test_run_backtest_pulls_strategy_id_from_context_and_calls_backtest(AXIOM_db, monkeypatch):
    from axiom.agents import tools_deepdive
    sid = _seed_strategy()
    tools_deepdive.set_deepdive_strategy(sid)

    captured = {}
    def fake_backtest(strategy_id, asset, strategy_type, params, bars=None, timeframe="1h",
                     persist_legacy_run=False, regime_gate=False):
        captured["sid"] = strategy_id
        captured["asset"] = asset
        captured["strategy_type"] = strategy_type
        captured["params"] = params
        captured["timeframe"] = timeframe
        captured["bars"] = bars
        return {"metrics": {"sharpe": 1.5, "win_rate": 0.55, "profit_factor": 1.8,
                             "total_return_pct": 0.12, "total_trades": 42,
                             "max_drawdown_pct": 0.08, "avg_bars_held": 7}}

    monkeypatch.setattr("axiom.strategies.backtest.backtest_strategy", fake_backtest)

    out = tools_deepdive._run_backtest(timeframe=None, bars=None)
    assert captured["sid"] == sid
    assert captured["asset"] == "BTC"
    assert captured["strategy_type"] == "rsi"
    assert captured["timeframe"] == "1h"
    parsed = json.loads(out)
    assert parsed["total_trades"] == 42
    assert parsed["sharpe"] == 1.5
    tools_deepdive.clear_deepdive_strategy()


def test_run_backtest_uses_runtime_type_over_type(AXIOM_db, monkeypatch):
    from axiom.agents import tools_deepdive
    sid = _seed_strategy(stype="legacy_type")
    with get_db() as conn:
        conn.execute("UPDATE strategies SET runtime_type = ? WHERE id = ?", ("modern_rt", sid))
        conn.commit()
    tools_deepdive.set_deepdive_strategy(sid)

    captured = {}
    def fake_backtest(strategy_id, asset, strategy_type, params, **kwargs):
        captured["strategy_type"] = strategy_type
        return {"metrics": {}}
    monkeypatch.setattr("axiom.strategies.backtest.backtest_strategy", fake_backtest)

    tools_deepdive._run_backtest(timeframe=None, bars=None)
    assert captured["strategy_type"] == "modern_rt"
    tools_deepdive.clear_deepdive_strategy()


def test_run_backtest_without_session_raises(AXIOM_db):
    from axiom.agents import tools_deepdive
    tools_deepdive.clear_deepdive_strategy()
    with pytest.raises(RuntimeError, match="no Deepdive strategy"):
        tools_deepdive._run_backtest(timeframe=None, bars=None)


def test_run_backtest_propagates_backtest_error(AXIOM_db, monkeypatch):
    from axiom.agents import tools_deepdive
    sid = _seed_strategy()
    tools_deepdive.set_deepdive_strategy(sid)
    monkeypatch.setattr(
        "axiom.strategies.backtest.backtest_strategy",
        lambda **kw: {"error": "no data for BTC 1h"},
    )
    out = tools_deepdive._run_backtest(timeframe=None, bars=None)
    assert "no data for BTC 1h" in out
    tools_deepdive.clear_deepdive_strategy()
