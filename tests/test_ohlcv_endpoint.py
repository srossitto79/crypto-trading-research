"""Regression: /api/ohlcv (the live OHLCV fallback the series drill-down relies on)
must not 500.

_normalize_asset_key / _coerce_iso_timestamp live in the trading domain, not
api_core; _build_ohlcv_response referenced them as ``core._*`` which raised
AttributeError and 500-ed the endpoint (surfacing as "Internal Server Error" in
the drill-down modal).
"""

from __future__ import annotations

import pandas as pd

from axiom.api_domains import data as dd


def test_get_ohlcv_no_attribute_error_on_slashed_symbol(monkeypatch):
    monkeypatch.setattr(dd, "fetch_hyperliquid_candles", lambda *a, **k: pd.DataFrame())
    out = dd.get_ohlcv(symbol="LINK/USDT", timeframe="15m", limit=5)
    assert out["symbol"] == "LINK/USDT"
    assert out["row_count"] == 0
    assert out["data"] == []


def test_get_ohlcv_maps_live_candles(monkeypatch):
    ts = pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [1.0, 1.0, 1.0],
            "high": [2.0, 2.0, 2.0],
            "low": [0.5, 0.5, 0.5],
            "close": [1.5, 1.5, 1.5],
            "volume": [10.0, 10.0, 10.0],
        },
        index=ts,
    )
    monkeypatch.setattr(dd, "fetch_hyperliquid_candles", lambda *a, **k: frame)
    out = dd.get_ohlcv(symbol="LINK/USDT", timeframe="15m", limit=5)
    assert out["row_count"] == 3
    assert len(out["data"]) == 3
    assert out["data"][0]["close"] == 1.5
    assert out["data"][0]["timestamp"]  # coerced to ISO, non-empty
