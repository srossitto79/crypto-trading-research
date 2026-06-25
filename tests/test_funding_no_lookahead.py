"""get_funding_for_backtest must not leak the future or fabricate funding.

Previously it returned records[-1] (the LATEST rate = future relative to an early
bar) when no exact window matched, then synthesized funding when the cache was
absent. Both corrupt the funding signal that funding-family strategies trade on.
"""
from __future__ import annotations

import json

from axiom.strategies import sentiment

_INTERVAL = 8 * 60 * 60 * 1000  # 8h


def _write_funding(dirpath, symbol, records):
    (dirpath / f"{symbol}_funding.json").write_text(json.dumps(records))


def test_no_future_leak_uses_in_effect_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(sentiment, "_FUNDING_CACHE_DIR", tmp_path)
    sentiment._load_funding_records.cache_clear()
    t0 = (1_700_000_000_000 // _INTERVAL) * _INTERVAL
    _write_funding(
        tmp_path,
        "BTCUSDT",
        [
            {"funding_time": t0, "funding_rate": 0.0001},
            {"funding_time": t0 + _INTERVAL, "funding_rate": 0.0002},
            {"funding_time": t0 + 2 * _INTERVAL, "funding_rate": 0.0003},
        ],
    )
    # A bar inside the FIRST window must get 0.0001 — never the latest 0.0003.
    assert sentiment.get_funding_for_backtest("BTC", t0 + 3_600_000) == 0.0001
    # A bar inside the second window gets the second rate.
    assert sentiment.get_funding_for_backtest("BTC", t0 + _INTERVAL + 100) == 0.0002


def test_before_any_funding_returns_zero_not_fabricated(monkeypatch, tmp_path):
    monkeypatch.setattr(sentiment, "_FUNDING_CACHE_DIR", tmp_path)
    sentiment._load_funding_records.cache_clear()
    t0 = (1_700_000_000_000 // _INTERVAL) * _INTERVAL
    _write_funding(tmp_path, "BTCUSDT", [{"funding_time": t0, "funding_rate": 0.0001}])
    assert sentiment.get_funding_for_backtest("BTC", t0 - _INTERVAL) == 0.0


def test_stale_gap_returns_zero_not_carried(monkeypatch, tmp_path):
    monkeypatch.setattr(sentiment, "_FUNDING_CACHE_DIR", tmp_path)
    sentiment._load_funding_records.cache_clear()
    t0 = (1_700_000_000_000 // _INTERVAL) * _INTERVAL
    _write_funding(tmp_path, "BTCUSDT", [{"funding_time": t0, "funding_rate": 0.0001}])
    # 20h after the only funding record — beyond the 16h staleness cap.
    assert sentiment.get_funding_for_backtest("BTC", t0 + 20 * 60 * 60 * 1000) == 0.0


def test_missing_cache_returns_zero_not_synthetic(monkeypatch, tmp_path):
    monkeypatch.setattr(sentiment, "_FUNDING_CACHE_DIR", tmp_path)
    sentiment._load_funding_records.cache_clear()
    assert sentiment.get_funding_for_backtest("DOGE", 1_700_000_000_000) == 0.0
