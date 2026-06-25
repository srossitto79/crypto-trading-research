from __future__ import annotations

import pandas as pd

from axiom.strategies.backtest import _vectorized_signals


def _stochastic_signal_frame() -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [10.0, 10.0, 10.0, 10.0],
            "stoch_k": [10.0, 25.0, 85.0, 75.0],
            "adx_val": [15.0, 30.0, 20.0, 35.0],
        },
        index=index,
    )


def test_stochastic_entry_respects_adx_min_filter() -> None:
    df = _stochastic_signal_frame()
    params = {"direction": "long", "k_oversold": 20, "k_overbought": 80}

    base_entry, _ = _vectorized_signals(df, "stochastic", params)
    filtered_entry, _ = _vectorized_signals(df, "stochastic", {**params, "adx_min": 35})

    assert base_entry.tolist() == [False, True, False, False]
    assert filtered_entry.tolist() == [False, False, False, False]


def test_stochastic_entry_respects_adx_max_filter() -> None:
    df = _stochastic_signal_frame()
    params = {"direction": "long", "k_oversold": 20, "k_overbought": 80}

    base_entry, _ = _vectorized_signals(df, "stochastic", params)
    filtered_entry, _ = _vectorized_signals(df, "stochastic", {**params, "adx_max": 25})

    assert base_entry.tolist() == [False, True, False, False]
    assert filtered_entry.tolist() == [False, False, False, False]
