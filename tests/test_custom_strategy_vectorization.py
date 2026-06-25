from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

ADXTrendStrategy = pytest.importorskip("axiom.strategies.custom.ADX_TREND").ADXTrendStrategy
ADXFilteredEMAStrategy = pytest.importorskip("axiom.strategies.custom.adx_filtered_ema").ADXFilteredEMAStrategy
ATRBreakoutVolumeComposite = pytest.importorskip("axiom.strategies.custom.atr_breakout_volume_composite").ATRBreakoutVolumeComposite
WilliamsRRsiStrategy = pytest.importorskip("axiom.strategies.custom.williams_r_rsi").WilliamsRRsiStrategy
ZscoreMeanReversionComposite = pytest.importorskip("axiom.strategies.custom.zscore_mean_reversion_composite").ZscoreMeanReversionComposite


def _market_frame(periods: int = 320, *, freq: str = "15min") -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq=freq, tz="UTC")
    trend = np.linspace(100.0, 122.0, periods)
    wave = 3.5 * np.sin(np.linspace(0.0, 10.0 * np.pi, periods))
    close = pd.Series(trend + wave, index=index, dtype=float)
    close.iloc[-4:] = [117.0, 119.5, 122.5, 126.0]

    open_ = close.shift(1).fillna(close.iloc[0] - 0.4)
    high = close + 0.9
    low = close - 0.9
    volume = pd.Series(
        1_000.0 + np.linspace(0.0, 250.0, periods) + (120.0 * np.sin(np.linspace(0.0, 6.0 * np.pi, periods))),
        index=index,
        dtype=float,
    ).abs()
    volume.iloc[-1] = float(volume.max() * 1.8)

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


@pytest.mark.parametrize(
    ("strategy", "frame"),
    [
        (
            ADXTrendStrategy(
                "adx-trend-test",
                {
                    "adx_length": 14,
                    "adx_threshold": 20,
                    "use_rsi_filter": False,
                    "use_ema_filter": False,
                },
            ),
            _market_frame(),
        ),
        (
            ADXFilteredEMAStrategy(
                "adx-filtered-ema-test",
                {
                    "fast_ema": 9,
                    "slow_ema": 21,
                    "adx_period": 14,
                    "adx_threshold": 1.0,
                },
            ),
            _market_frame(),
        ),
        (
            WilliamsRRsiStrategy(
                "wr-rsi-test",
                {
                    "wr_period": 14,
                    "wr_oversold": -75,
                    "wr_overbought": -25,
                    "rsi_period": 14,
                    "rsi_oversold": 35,
                    "rsi_overbought": 65,
                    "ema_period": 50,
                    "use_trend_filter": False,
                },
            ),
            _market_frame(),
        ),
        (
            ZscoreMeanReversionComposite(
                "zscore-test",
                {
                    "zscore_window": 20,
                    "zscore_entry": 1.5,
                    "zscore_exit": 0.5,
                    "rsi_length": 14,
                    "rsi_oversold": 35,
                    "rsi_overbought": 65,
                    "atr_length": 14,
                    "ema_confirm": 20,
                    "min_volume_ratio": 0.0,
                },
            ),
            _market_frame(),
        ),
        (
            ATRBreakoutVolumeComposite(
                "atr-breakout-volume-test",
                {
                    "atr_period": 14,
                    "atr_multiplier": 0.75,
                    "volume_sma_period": 20,
                },
            ),
            _market_frame(),
        ),
    ],
)
def test_custom_vectorized_signals_match_generate_signal_last_bar(strategy, frame):
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
