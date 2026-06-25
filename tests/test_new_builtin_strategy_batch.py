from __future__ import annotations

import numpy as np
import pandas as pd

from axiom.strategies.backtest import backtest_strategy
from axiom.strategies.builtin.adx_trend_pulse import ADXTrendPulseStrategy
from axiom.strategies.builtin.atr_volume_breakout import ATRVolumeBreakoutStrategy
from axiom.strategies.builtin.ema_rsi_pullback import EMARSIPullbackStrategy
from axiom.strategies.builtin.williams_ema_reclaim import WilliamsEMAReclaimStrategy
from axiom.strategies.builtin.zscore_mean_reclaim import ZScoreMeanReclaimStrategy
from axiom.strategies.registry import discover, get_all, reset
from axiom.db import init_db


def _market_frame(periods: int = 360, *, freq: str = "1h") -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq=freq, tz="UTC")
    trend = np.linspace(100.0, 138.0, periods)
    wave = 4.5 * np.sin(np.linspace(0.0, 16.0 * np.pi, periods))
    close = pd.Series(trend + wave, index=index, dtype=float)
    close.iloc[-6:] = [129.0, 128.0, 127.5, 130.5, 133.0, 136.0]

    open_ = close.shift(1).fillna(close.iloc[0] - 0.5)
    high = close + 1.1
    low = close - 1.1
    volume = pd.Series(
        1_500.0
        + np.linspace(0.0, 400.0, periods)
        + (180.0 * np.sin(np.linspace(0.0, 8.0 * np.pi, periods))),
        index=index,
        dtype=float,
    ).abs()
    volume.iloc[-4:] = [1800.0, 2200.0, 2800.0, 3600.0]

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def test_new_strategy_types_are_discoverable():
    reset()
    discover(include_custom=False)
    runtime_types = {strategy.strategy_type for strategy in get_all().values()}

    assert "trend_pulse_adx" in runtime_types
    assert "atr_volume_breakout" in runtime_types
    assert "ema_rsi_pullback" in runtime_types
    assert "williams_ema_reclaim" in runtime_types
    assert "zscore_mean_reclaim" in runtime_types


def test_new_builtin_strategies_vectorized_signals_match_last_bar():
    frame = _market_frame()
    strategies = [
        ADXTrendPulseStrategy(
            "adx-trend-pulse-test",
            {
                "ema_fast": 12,
                "ema_slow": 26,
                "adx_period": 14,
                "adx_threshold": 12.0,
                "pullback_bars": 3,
            },
        ),
        ATRVolumeBreakoutStrategy(
            "atr-volume-breakout-test",
            {
                "atr_period": 14,
                "atr_multiplier": 0.8,
                "volume_period": 20,
                "volume_multiplier": 1.1,
                "breakout_lookback": 20,
            },
        ),
        EMARSIPullbackStrategy(
            "ema-rsi-pullback-test",
            {
                "ema_fast": 10,
                "ema_slow": 30,
                "rsi_period": 8,
                "rsi_entry": 48,
                "rsi_exit": 62,
            },
        ),
        WilliamsEMAReclaimStrategy(
            "williams-ema-reclaim-test",
            {
                "wr_period": 14,
                "ema_period": 21,
                "wr_entry": -65.0,
                "wr_exit": -25.0,
            },
        ),
        ZScoreMeanReclaimStrategy(
            "zscore-mean-reclaim-test",
            {
                "zscore_window": 20,
                "zscore_entry": -1.2,
                "zscore_exit": 0.2,
                "ema_period": 34,
            },
        ),
    ]

    for strategy in strategies:
        signal = strategy.generate_signal(frame)
        entry_signals, exit_signals = strategy.generate_signals(frame)

        assert isinstance(entry_signals, pd.Series)
        assert isinstance(exit_signals, pd.Series)
        assert len(entry_signals) == len(frame)
        assert len(exit_signals) == len(frame)
        assert pd.api.types.is_bool_dtype(entry_signals)
        assert pd.api.types.is_bool_dtype(exit_signals)
        assert bool(entry_signals.iloc[-1]) is signal.entry_signal
        assert bool(exit_signals.iloc[-1]) is signal.exit_signal


def test_new_builtin_strategies_backtest_on_shared_engine():
    init_db()
    frame = _market_frame(periods=420)
    cases = [
        (
            "ADXTRENDPULSE-BTC",
            "BTC",
                "trend_pulse_adx",
            {
                "_asset": "BTC",
                "ema_fast": 12,
                "ema_slow": 26,
                "adx_period": 14,
                "adx_threshold": 12.0,
                "pullback_bars": 3,
            },
        ),
        (
            "ATRVOLBREAK-ETH",
            "ETH",
            "atr_volume_breakout",
            {
                "_asset": "ETH",
                "atr_period": 14,
                "atr_multiplier": 0.8,
                "volume_period": 20,
                "volume_multiplier": 1.1,
                "breakout_lookback": 20,
            },
        ),
        (
            "EMARSI-SOL",
            "SOL",
            "ema_rsi_pullback",
            {
                "_asset": "SOL",
                "ema_fast": 10,
                "ema_slow": 30,
                "rsi_period": 8,
                "rsi_entry": 48,
                "rsi_exit": 62,
            },
        ),
        (
            "WREMA-BTC",
            "BTC",
            "williams_ema_reclaim",
            {
                "_asset": "BTC",
                "wr_period": 14,
                "ema_period": 21,
                "wr_entry": -65.0,
                "wr_exit": -25.0,
            },
        ),
        (
            "ZSCORE-ETH",
            "ETH",
            "zscore_mean_reclaim",
            {
                "_asset": "ETH",
                "zscore_window": 20,
                "zscore_entry": -1.2,
                "zscore_exit": 0.2,
                "ema_period": 34,
            },
        ),
    ]

    for strategy_id, asset, strategy_type, params in cases:
        result = backtest_strategy(
            strategy_id=strategy_id,
            asset=asset,
            strategy_type=strategy_type,
            params=params,
            bars=len(frame),
            candles_df=frame,
            leverage=1.0,
            fee_bps=0.0,
            slippage_bps=0.0,
            persist_legacy_run=False,
            regime_gate=False,
            sync_strategy_state=False,
        )

        assert "error" not in result
        assert isinstance(result["trades"], list)
        assert result["metrics"]["total_trades"] >= 0
