from __future__ import annotations

import pandas as pd

import axiom.strategies.backtest as backtest_mod


def test_vectorized_williams_r_applies_adx_max_to_entries():
    """Williams %R is a mean-reversion strategy: adx_max caps ADX to range-bound conditions.

    adx_val=[10, 15, 15, 30, 30, 30, 30]
    williams_r=[-90, -85, -79, -85, -79, -19, -25]
    Oversold cross-up entries at bars 2 and 4 (prev < -80, curr >= -80).

    With adx_max=100 (permissive):  bars 2 and 4 both pass → 2 entries.
    With adx_max=25 (tight cap):    bar 2 passes (adx=15 <= 25),
                                     bar 4 blocked (adx=30 > 25) → 1 entry.
    """
    index = pd.date_range("2026-01-01", periods=7, freq="h", tz="UTC")
    frame = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 104.0],
            "williams_r": [-90.0, -85.0, -79.0, -85.0, -79.0, -19.0, -25.0],
            "adx_val": [10.0, 15.0, 15.0, 30.0, 30.0, 30.0, 30.0],
        },
        index=index,
    )

    entry_hi, exit_hi = backtest_mod._vectorized_signals(
        frame,
        "williams_r",
        {
            "williams_r_oversold": -80,
            "williams_r_overbought": -20,
            "adx_max": 100,
        },
    )
    entry_lo, exit_lo = backtest_mod._vectorized_signals(
        frame,
        "williams_r",
        {
            "williams_r_oversold": -80,
            "williams_r_overbought": -20,
            "adx_max": 25,
        },
    )

    # Permissive cap: both oversold cross-ups trigger
    assert int(entry_hi.fillna(False).sum()) == 2
    assert bool(entry_hi.loc[index[2]])
    assert bool(entry_hi.loc[index[4]])

    # Tight cap: only bar 2 (ADX 15 <= 25) passes; bar 4 (ADX 30) blocked
    assert int(entry_lo.fillna(False).sum()) == 1
    assert bool(entry_lo.loc[index[2]])
    assert not bool(entry_lo.loc[index[4]])

    # Exits are identical regardless of ADX cap
    assert int(exit_hi.fillna(False).sum()) == 1
    assert exit_hi.fillna(False).equals(exit_lo.fillna(False))


def test_vectorized_williams_r_adx_threshold_alias_maps_to_adx_max():
    """adx_threshold should be aliased to adx_max for williams_r (mean-reversion)."""
    index = pd.date_range("2026-01-01", periods=7, freq="h", tz="UTC")
    frame = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 104.0],
            "williams_r": [-90.0, -85.0, -79.0, -85.0, -79.0, -19.0, -25.0],
            "adx_val": [10.0, 15.0, 15.0, 30.0, 30.0, 30.0, 30.0],
        },
        index=index,
    )

    # adx_threshold=25 for williams_r should mean adx_max=25 (cap, not floor)
    entry, _ = backtest_mod._vectorized_signals(
        frame,
        "williams_r",
        {
            "williams_r_oversold": -80,
            "williams_r_overbought": -20,
            "adx_threshold": 25,
        },
    )

    # Bar 2: ADX=15 <= 25 → allowed (mean-reversion wants low ADX)
    assert bool(entry.loc[index[2]])
    # Bar 4: ADX=30 > 25 → blocked
    assert not bool(entry.loc[index[4]])
