"""Smoke test: funding enrichment → signal check end-to-end.

Verifies that when funding/OI parquet files are present, data_manager.enrich()
correctly adds funding_rate and open_interest columns, and a funding_direction
signal check can run against the enriched DataFrame without raising an exception.

Does NOT call backtest_strategy() (too heavy) — tests the enrichment + signal
layer directly.
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from axiom.data_manager import DataManager, _save_stream_parquet
from axiom.strategies import backtest as backtest_mod


def _make_ohlcv(n: int = 100) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": [100.0 + i * 0.1 for i in range(n)],
        "volume": 1000.0,
    })


def _make_funding(n: int = 100) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "funding_rate": [0.0001 * ((-1) ** i) for i in range(n)],
    })


def _make_oi(n: int = 100) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open_interest": [1_000_000.0 + i * 100 for i in range(n)],
    })


def test_enrich_adds_funding_and_oi_columns(tmp_path):
    """data_manager.enrich() adds funding_rate and open_interest when parquet files exist."""
    dm = DataManager()
    ohlcv = _make_ohlcv(100)

    funding_path = tmp_path / "funding" / "ETH-USDT" / "history.parquet"
    _save_stream_parquet(_make_funding(100), funding_path, "funding", "ETH-USDT")

    oi_path = tmp_path / "oi" / "ETH-USDT" / "1h.parquet"
    _save_stream_parquet(_make_oi(100), oi_path, "oi", "ETH-USDT")

    with patch("axiom.data_manager.FUNDING_DIR", tmp_path / "funding"):
        with patch("axiom.data_manager.OI_DIR", tmp_path / "oi"):
            enriched = dm.enrich(ohlcv, "ETH-USDT", "1h")

    assert "funding_rate" in enriched.columns, "funding_rate column missing after enrich"
    assert "open_interest" in enriched.columns, "open_interest column missing after enrich"
    assert len(enriched) == len(ohlcv), "enrich must not drop rows"


def test_funding_direction_signal_runs_on_enriched_df(tmp_path):
    """funding_direction signal check completes without exception on enriched DataFrame."""
    dm = DataManager()
    ohlcv = _make_ohlcv(100)

    funding_path = tmp_path / "funding" / "BTC-USDT" / "history.parquet"
    _save_stream_parquet(_make_funding(100), funding_path, "funding", "BTC-USDT")

    oi_path = tmp_path / "oi" / "BTC-USDT" / "1h.parquet"
    _save_stream_parquet(_make_oi(100), oi_path, "oi", "BTC-USDT")

    with patch("axiom.data_manager.FUNDING_DIR", tmp_path / "funding"), \
         patch("axiom.data_manager.OI_DIR", tmp_path / "oi"), \
         patch("axiom.data_manager.DERIVATIVES_DIR", tmp_path / "derivatives"), \
         patch("axiom.data_manager.MACRO_DIR", tmp_path / "macro"):
        enriched = dm.enrich(ohlcv, "BTC-USDT", "1h")

    assert enriched["funding_rate"].notna().any(), "expected non-null funding_rate values"

    with patch("axiom.scanner.fetch_hyperliquid_funding_rate", return_value=0.0001):
        from axiom.scanner import check_funding_direction_signal
        result = check_funding_direction_signal(enriched, {}, coin="BTC")

    assert isinstance(result, dict), "signal checker must return a dict"
    assert "entry_signal" in result
    assert "direction" in result


def test_enrich_with_no_parquet_files_does_not_raise(tmp_path):
    """enrich() is fully graceful when funding/OI parquet files are absent."""
    dm = DataManager()
    ohlcv = _make_ohlcv(50)

    with patch("axiom.data_manager.FUNDING_DIR", tmp_path / "funding"):
        with patch("axiom.data_manager.OI_DIR", tmp_path / "oi"):
            result = dm.enrich(ohlcv, "BTC-USDT", "1h")

    assert len(result) == len(ohlcv)
    assert "funding_rate" not in result.columns
    assert "open_interest" not in result.columns


def test_backtest_strategy_accepts_datetime_index_for_funding(AXIOM_db, monkeypatch):
    """Funding backtests should accept normalized candle frames indexed by timestamp."""
    candles = _make_ohlcv(240).set_index("timestamp")
    observed: dict[str, object] = {}

    monkeypatch.setattr(backtest_mod, "_should_use_process_isolation", lambda: False)
    monkeypatch.setattr(
        "axiom.strategies.sentiment.get_funding_for_backtest",
        lambda *_args, **_kwargs: 0.0001,
    )

    def _fake_worker(*args, **kwargs):
        df = args[4]
        observed["has_funding_rate"] = "funding_rate" in df.columns
        observed["funding_values"] = sorted(set(round(float(v), 6) for v in df["funding_rate"].tail(3)))
        raise RuntimeError("sentinel worker stop")

    monkeypatch.setattr(backtest_mod, "_isolated_backtest_worker", _fake_worker)

    with pytest.raises(RuntimeError, match="sentinel worker stop"):
        backtest_mod.backtest_strategy(
            strategy_id="S-TEST-FUND",
            asset="BTC/USDT",
            strategy_type="funding",
            params={"_asset": "BTC"},
            bars=240,
            timeframe="1h",
            candles_df=candles,
            persist_legacy_run=False,
            regime_gate=False,
        )

    assert observed["has_funding_rate"] is True
    assert observed["funding_values"] == [0.0001]
