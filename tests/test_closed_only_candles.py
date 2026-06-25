"""Closed-only candle enforcement at the OHLCV write boundary.

The REST collector fetches up to now+tf, so the last fetched row is typically the
in-progress (forming) bar. Persisting it repaints/leaks lookahead into backtests
that read between fetches. _drop_unclosed_bars must remove it at the write boundary.
"""
from __future__ import annotations

import pandas as pd

from axiom.data import _drop_unclosed_bars


def _frame(ts_ms: list[int]) -> pd.DataFrame:
    n = len(ts_ms)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(ts_ms, unit="ms", utc=True),
            "open": [1.0] * n,
            "high": [1.0] * n,
            "low": [1.0] * n,
            "close": [1.0] * n,
            "volume": [1.0] * n,
        }
    )


def test_drops_forming_bar_keeps_last_closed():
    tf = 3_600_000  # 1h
    now = (1_700_000_000_000 // tf) * tf  # aligned: `now` is a fresh bar open
    df = _frame([now - 2 * tf, now - tf, now])  # last row = forming bar
    out = _drop_unclosed_bars(df, tf, now)
    kept = set(out["timestamp"].tolist())
    assert pd.Timestamp(now, unit="ms", tz="UTC") not in kept  # forming dropped
    assert pd.Timestamp(now - tf, unit="ms", tz="UTC") in kept  # last closed kept
    assert len(out) == 2


def test_all_closed_frame_unchanged():
    tf = 3_600_000
    now = (1_700_000_000_000 // tf) * tf
    df = _frame([now - 3 * tf, now - 2 * tf, now - tf])  # all closed
    out = _drop_unclosed_bars(df, tf, now)
    assert len(out) == 3


def test_cleans_previously_persisted_forming_bar():
    # Even if a stale forming bar made it into the lake before the fix, the next
    # write drops it (it's still unclosed relative to the same `now`).
    tf = 900_000  # 15m
    now = (1_700_000_000_000 // tf) * tf
    df = _frame([now - tf, now])
    out = _drop_unclosed_bars(df, tf, now)
    assert len(out) == 1
    assert out["timestamp"].iloc[-1] == pd.Timestamp(now - tf, unit="ms", tz="UTC")


def test_empty_safe():
    assert _drop_unclosed_bars(pd.DataFrame(), 3_600_000, 1_700_000_000_000).empty


def test_reject_invalid_ohlc_drops_bad_bars():
    from axiom.data import _reject_invalid_ohlc

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([0, 1, 2, 3, 4], unit="ms", utc=True),
            "open":   [10, 10, 10, 10, 10],
            "high":   [12,  9, 12, 12, 12],  # row1: high<open
            "low":    [ 8,  8,  8,  8,  8],
            "close":  [11, 11, 99, -1, 11],  # row2: close>high; row3: close<=0 & <low
            "volume": [ 1,  1,  1,  1, -5],   # row4: negative volume
        }
    )
    out = _reject_invalid_ohlc(df)
    assert len(out) == 1  # only the first row satisfies every invariant
    assert out["close"].iloc[0] == 11


def test_reject_invalid_ohlc_keeps_clean_frame():
    from axiom.data import _reject_invalid_ohlc

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([0, 3_600_000], unit="ms", utc=True),
            "open": [10.0, 11.0],
            "high": [12.0, 13.0],
            "low": [9.0, 10.0],
            "close": [11.0, 12.0],
            "volume": [5.0, 0.0],
        }
    )
    out = _reject_invalid_ohlc(df)
    assert len(out) == 2


def test_get_dataset_source_round_trips_parquet_metadata(monkeypatch, tmp_path):
    from axiom import data as d

    if not d._using_pyarrow():
        import pytest

        pytest.skip("pyarrow required for parquet metadata")
    monkeypatch.setattr(d, "DATA_DIR", tmp_path)
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([0, 3_600_000], unit="ms", utc=True),
            "open": [10.0, 10.0],
            "high": [12.0, 12.0],
            "low": [9.0, 9.0],
            "close": [11.0, 11.0],
            "volume": [1.0, 1.0],
        }
    )
    d.save_parquet(df, "BTC-USDT", "1h", source="binanceusdm")
    assert d.get_dataset_source("BTC-USDT", "1h") == "binanceusdm"
    assert d.get_dataset_source("NOPE-USDT", "1h") is None
