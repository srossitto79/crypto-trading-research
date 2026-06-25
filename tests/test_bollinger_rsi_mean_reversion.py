from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from axiom.strategies.base import DirectionalSignals

# Axiom/strategies/custom/ is gitignored and AI-rewritten at runtime: the
# module may be absent OR define a different class. Guard BOTH, otherwise a
# collection-time AttributeError aborts the entire pytest session (audit M-4).
_mod = pytest.importorskip("axiom.strategies.custom.bollinger_rsi_mean_reversion")
BollingerRSIMeanReversion = getattr(_mod, "BollingerRSIMeanReversion", None)
if BollingerRSIMeanReversion is None:
    pytest.skip(
        "custom module no longer defines BollingerRSIMeanReversion (AI-rewritten)",
        allow_module_level=True,
    )


def _market_frame(periods: int, *, freq: str = "5min", seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2025-01-01", periods=periods, freq=freq, tz="UTC")
    drift = np.linspace(100.0, 140.0, periods)
    wave = 4.0 * np.sin(np.linspace(0.0, 18.0 * np.pi, periods))
    noise = rng.normal(0.0, 0.6, size=periods)
    close = pd.Series(drift + wave + noise, index=index, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0] - 0.2)
    high = close + 0.5
    low = close - 0.5
    volume = pd.Series(1000.0 + rng.uniform(0, 300, size=periods), index=index)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _default_params() -> dict:
    return {
        "bb_length": 20,
        "bb_std": 2.0,
        "rsi_length": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
    }


def _entry_exit(signals: DirectionalSignals) -> tuple[pd.Series, pd.Series]:
    """Collapse the dual-side payload to combined entry/exit masks, matching
    how generate_signal derives its scalar entry/exit (long OR short)."""
    entries = (signals.long_entries | signals.short_entries).astype(bool)
    exits = (signals.long_exits | signals.short_exits).astype(bool)
    return entries, exits


def test_generate_signals_matches_generate_signal_last_bar():
    frame = _market_frame(320)
    strategy = BollingerRSIMeanReversion("bb-rsi-mr-parity", _default_params())

    signal = strategy.generate_signal(frame)
    signals = strategy.generate_signals(frame)

    assert isinstance(signals, DirectionalSignals)
    entry_signals, exit_signals = _entry_exit(signals)
    assert len(entry_signals) == len(frame)
    assert len(exit_signals) == len(frame)
    assert pd.api.types.is_bool_dtype(entry_signals)
    assert pd.api.types.is_bool_dtype(exit_signals)
    assert bool(entry_signals.iloc[-1]) == bool(signal.entry_signal)
    assert bool(exit_signals.iloc[-1]) == bool(signal.exit_signal)


def test_generate_signals_short_frame_returns_empty_bool_series():
    frame = _market_frame(10)
    strategy = BollingerRSIMeanReversion("bb-rsi-mr-short", _default_params())

    signals = strategy.generate_signals(frame)
    entry_signals, exit_signals = _entry_exit(signals)

    assert not entry_signals.any()
    assert not exit_signals.any()
    assert len(entry_signals) == len(frame)
    assert len(exit_signals) == len(frame)


def test_generate_signals_large_frame_runs_under_one_second():
    frame = _market_frame(105_000)
    strategy = BollingerRSIMeanReversion("bb-rsi-mr-perf", _default_params())

    t0 = time.perf_counter()
    signals = strategy.generate_signals(frame)
    elapsed = time.perf_counter() - t0

    entry_signals, exit_signals = _entry_exit(signals)
    assert len(entry_signals) == len(frame)
    assert len(exit_signals) == len(frame)
    assert elapsed < 1.0, f"vectorized path regressed: {elapsed:.2f}s for 105k bars"
