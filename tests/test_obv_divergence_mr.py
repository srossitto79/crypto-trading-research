from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

OBVDivergenceMRStrategy = pytest.importorskip(
    "axiom.strategies.custom.obv_divergence_mr"
).OBVDivergenceMRStrategy


def _market_frame(periods: int, *, freq: str = "5min", seed: int = 43) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2025-01-01", periods=periods, freq=freq, tz="UTC")
    drift = np.linspace(4.0, 5.5, periods)
    wave = 0.25 * np.sin(np.linspace(0.0, 24.0 * np.pi, periods))
    noise = rng.normal(0.0, 0.04, size=periods)
    close = pd.Series(drift + wave + noise, index=index, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.03
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.03
    volume = pd.Series(10_000.0 + rng.uniform(0, 4_000, size=periods), index=index)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _ui_alias_params() -> dict:
    return {
        "bb_std": 2,
        "bb_window": 20,
        "divergence_window": 20,
        "max_hold_bars": 10,
        "price_move_threshold": 0.05,
        "volume_window": 20,
    }


def test_generate_signals_matches_generate_signal_last_bar_with_ui_aliases():
    frame = _market_frame(320)
    strategy = OBVDivergenceMRStrategy("obv-divergence-mr-parity", _ui_alias_params())

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


def test_generate_signals_large_five_minute_year_runs_quickly():
    frame = _market_frame(105_000)
    strategy = OBVDivergenceMRStrategy("obv-divergence-mr-perf", _ui_alias_params())

    t0 = time.perf_counter()
    entry_signals, exit_signals = strategy.generate_signals(frame)
    elapsed = time.perf_counter() - t0

    assert len(entry_signals) == len(frame)
    assert len(exit_signals) == len(frame)
    assert elapsed < 2.0, f"vectorized path regressed: {elapsed:.2f}s for 105k bars"
