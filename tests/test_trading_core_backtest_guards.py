from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import axiom.strategies.backtest as backtest_mod


def _fake_ohlcv(n: int) -> pd.DataFrame:
    index = pd.date_range(datetime.now(timezone.utc), periods=n, freq="h")
    close = pd.Series(np.linspace(100.0, 120.0, n), index=index)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.linspace(1_000.0, 2_000.0, n),
        },
        index=index,
    )


def _metrics_stub(trades: list[dict], total_bars: int, **_kwargs) -> dict:
    return {
        "total_trades": len(trades),
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown_pct": 0.0,
        "profit_factor": 0.0,
        "total_return_pct": 0.0,
        "avg_trade_pct": 0.0,
        "avg_bars_held": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "backtest_months": 0.0,
        "monthly_return_pct": 0.0,
        "annualized_return_pct": 0.0,
        "start_date": None,
        "end_date": None,
        "bars": total_bars,
    }


def test_compute_metrics_clamps_near_zero_sharpe():
    trades = [
        {"pnl_pct": 0.0100000, "bars_held": 1},
        {"pnl_pct": 0.0100001, "bars_held": 1},
        {"pnl_pct": 0.0100002, "bars_held": 1},
    ]

    metrics = backtest_mod.compute_metrics(trades, total_bars=8_760, timeframe="1h")

    assert metrics["sharpe"] == 0.0
    assert metrics["sortino"] == 0.0


def test_precompute_regimes_is_prefix_invariant():
    frame = _fake_ohlcv(360)

    full = backtest_mod._precompute_regimes(frame)
    prefix = backtest_mod._precompute_regimes(frame.iloc[:300])

    pd.testing.assert_series_equal(full.iloc[:300], prefix)


def test_run_signal_walk_enforces_range_bound_regime_gate(monkeypatch):
    frame = _fake_ohlcv(260)
    frame["adx_val"] = 30.0
    frame.loc[frame.index[230:233], "adx_val"] = 10.0

    regimes = pd.Series(backtest_mod.TREND_UP, index=frame.index)
    regimes.iloc[230:233] = backtest_mod.RANGE_BOUND

    class _AlwaysEntryStrategy:
        def __init__(self):
            self.params = {}
            self.compatible_regimes = {backtest_mod.TREND_UP, backtest_mod.RANGE_BOUND}

        def generate_signal(self, window):
            price = float(window["close"].iloc[-1])
            return {"price": price, "entry_signal": True, "exit_signal": False}

    monkeypatch.setattr(backtest_mod, "_precompute_regimes", lambda _df: regimes)
    monkeypatch.setattr(
        backtest_mod,
        "_run_vectorized_backtest",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(backtest_mod._VECTORIZED_PATH_UNAVAILABLE)
        ),
    )

    trades = backtest_mod._run_signal_walk(
        checker=None,
        df=frame,
        params={},
        warmup=210,
        leverage=1.0,
        strategy_obj=_AlwaysEntryStrategy(),
        strategy_type="stochastic",
        fee_bps=0.0,
        slippage_bps=0.0,
    )

    assert len(trades) == 1
    # The entry signal fires on bar 230 (RANGE_BOUND window), but a signal derived
    # from bar 230's close can only be filled at bar 231's OPEN (next-bar-open), not
    # bar 230's close. The slow walk now matches the canonical vectorized path's
    # economics, so the trade is stamped at the fill bar (231), not the signal bar.
    assert trades[0]["entry_bar"] == 231
    assert trades[0]["bars_held"] == 3
    assert trades[0]["regime"] == backtest_mod.RANGE_BOUND
    assert trades[0]["entry_time"] == str(frame.index[231])
    assert trades[0]["exit_time"] == str(frame.index[234])


def test_build_regime_gate_masks_supports_trending_and_transitional_filters():
    index = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    frame = pd.DataFrame({"adx_val": [12.0, 35.0, 22.0, 38.0]}, index=index)
    regimes = pd.Series(
        [
            backtest_mod.RANGE_BOUND,
            backtest_mod.TREND_UP,
            backtest_mod.HIGH_VOL,
            backtest_mod.TREND_DOWN,
        ],
        index=index,
    )

    trending_allowed, trending_forced_exit, _ = backtest_mod._build_regime_gate_masks(
        frame,
        "stochastic",
        {"regime_filter": "TRENDING"},
        regimes=regimes,
    )
    transitional_allowed, transitional_forced_exit, _ = backtest_mod._build_regime_gate_masks(
        frame,
        "stochastic",
        {"regime_filter": "TRANSITIONAL"},
        regimes=regimes,
    )

    assert trending_allowed.tolist() == [False, True, False, True]
    assert trending_forced_exit.tolist() == [True, False, True, False]
    assert transitional_allowed.tolist() == [False, False, True, False]
    assert transitional_forced_exit.tolist() == [True, True, False, True]


def test_resolve_regime_gate_respects_explicit_trending_filter_for_mean_reversion():
    compatible, adx_min, adx_cap = backtest_mod.resolve_regime_gate(
        "stochastic",
        {"regime_filter": "TRENDING"},
        compatible_regimes={backtest_mod.TREND_UP, backtest_mod.TREND_DOWN, backtest_mod.RANGE_BOUND},
    )

    assert compatible == {backtest_mod.TREND_UP, backtest_mod.TREND_DOWN}
    assert adx_min is None
    assert adx_cap is None


def test_backtest_strategy_filters_pre_boundary_oos_trades(monkeypatch):
    frame = _fake_ohlcv(420)
    split_idx = int(len(frame) * 0.70)
    boundary = frame.index[split_idx]
    call_counter = {"count": 0}

    monkeypatch.setattr("axiom.api_core.get_settings", lambda: {})
    monkeypatch.setattr(backtest_mod, "load_backtest_candles", lambda **_kwargs: frame)

    def _fake_run_signal_walk(*args, **kwargs):
        call_counter["count"] += 1
        window = args[1]
        if call_counter["count"] == 1:
            return [{"entry_time": window.index[-1].isoformat(), "pnl_pct": 0.1, "bars_held": 1}]
        return [
            {"entry_time": (boundary - pd.Timedelta(hours=1)).isoformat(), "pnl_pct": 0.1, "bars_held": 1},
            {"entry_time": (boundary + pd.Timedelta(hours=1)).isoformat(), "pnl_pct": 0.2, "bars_held": 1},
        ]

    monkeypatch.setattr(backtest_mod, "_run_signal_walk", _fake_run_signal_walk)
    monkeypatch.setattr(backtest_mod, "compute_metrics", _metrics_stub)

    result = backtest_mod.backtest_strategy(
        strategy_id="bt-oos-filter",
        asset="BTC",
        strategy_type="rsi_momentum",
        params={},
        bars=len(frame),
    )

    assert result["metrics"]["out_of_sample"]["total_trades"] == 1
    assert result["metrics"]["total_trades"] == 1


def test_walk_forward_uses_stricter_robustness_threshold(monkeypatch):
    frame = _fake_ohlcv(1_000)

    monkeypatch.setattr("axiom.api_core.get_settings", lambda: {})
    monkeypatch.setattr(backtest_mod, "load_backtest_candles", lambda **_kwargs: frame)

    def _fake_run_signal_walk(*args, **kwargs):
        window = args[1]
        return [
            {"entry_time": window.index[5].isoformat(), "pnl_pct": 0.05, "bars_held": 1},
            {"entry_time": window.index[-1].isoformat(), "pnl_pct": 0.10, "bars_held": 1},
        ]

    def _fake_compute_metrics(trades: list[dict], total_bars: int, **_kwargs) -> dict:
        metrics = _metrics_stub(trades, total_bars)
        metrics["sharpe"] = 2.0 if total_bars >= 300 else 0.2
        metrics["total_trades"] = len(trades)
        return metrics

    monkeypatch.setattr(backtest_mod, "_run_signal_walk", _fake_run_signal_walk)
    monkeypatch.setattr(backtest_mod, "compute_metrics", _fake_compute_metrics)

    result = backtest_mod.walk_forward(
        strategy_id="wf-threshold",
        asset="BTC",
        strategy_type="rsi_momentum",
        params={},
        total_bars=len(frame),
        n_splits=2,
    )

    assert result["aggregate_oos"]["total_trades"] == 2
    assert result["degradation"] == 0.9
    assert not bool(result["robust"])
    assert result["verdict"] == "FAIL"


@pytest.mark.parametrize("strategy_type", ["inside_bar", "donchian", "donchian_regime", "parabolic_sar"])
def test_certified_strategy_types_backtest_without_unknown_type_errors(strategy_type, monkeypatch):
    frame = _fake_ohlcv(320)

    monkeypatch.setattr("axiom.api_core.get_settings", lambda: {})
    monkeypatch.setattr(backtest_mod, "load_backtest_candles", lambda **_kwargs: frame)
    monkeypatch.setattr(backtest_mod, "_run_signal_walk", lambda *args, **kwargs: [])
    monkeypatch.setattr(backtest_mod, "compute_metrics", _metrics_stub)

    result = backtest_mod.backtest_strategy(
        strategy_id=f"bt-{strategy_type}",
        asset="BTC",
        strategy_type=strategy_type,
        params={},
        bars=len(frame),
    )

    assert "error" not in result
