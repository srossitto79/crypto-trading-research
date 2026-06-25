"""OHLCV keep-alive picks the stalest pairs first, not a blind round-robin.

Previously a cursor refreshed N fixed pairs/run, so with max_pairs_per_run=1 and
many pairs each one only refreshed every N runs (hours stale while green).
"""
from __future__ import annotations

import os


def _dm():
    from axiom.data_manager import get_data_manager

    return get_data_manager()


def test_no_cap_returns_all_pairs():
    dm = _dm()
    pairs = [("BTC-USDT", "1h"), ("ETH-USDT", "1h")]
    assert dm._select_keepalive_pairs(pairs, None) == pairs
    assert dm._select_keepalive_pairs(pairs, 5) == pairs  # len <= cap


def test_selects_least_recently_written_first(monkeypatch, tmp_path):
    import axiom.data as data_mod

    dm = _dm()
    pairs = [("BTC-USDT", "1h"), ("ETH-USDT", "1h"), ("SOL-USDT", "1h")]
    files = {}
    for i, p in enumerate(pairs):
        f = tmp_path / f"{p[0]}_{p[1]}.parquet"
        f.write_text("x")
        os.utime(f, (1000 + i * 100, 1000 + i * 100))  # BTC oldest, SOL newest
        files[p] = f
    monkeypatch.setattr(data_mod, "parquet_path", lambda s, t: files[(s, t)])

    selected = dm._select_keepalive_pairs(pairs, 2)
    assert set(selected) == {("BTC-USDT", "1h"), ("ETH-USDT", "1h")}
    assert ("SOL-USDT", "1h") not in selected  # freshest is deprioritized


def test_never_written_pair_ranks_most_stale(monkeypatch, tmp_path):
    import axiom.data as data_mod

    dm = _dm()
    fresh = tmp_path / "fresh.parquet"
    fresh.write_text("x")
    os.utime(fresh, (9_000_000, 9_000_000))
    missing = tmp_path / "does_not_exist.parquet"

    def _pp(symbol, timeframe):
        return fresh if symbol == "BTC-USDT" else missing

    monkeypatch.setattr(data_mod, "parquet_path", _pp)
    pairs = [("BTC-USDT", "1h"), ("NEW-USDT", "1h")]
    # cap of 1 -> the never-written NEW-USDT (stat fails -> most stale) wins.
    assert dm._select_keepalive_pairs(pairs, 1) == [("NEW-USDT", "1h")]
