"""Tests for universal symbol mapping."""

from __future__ import annotations

import pytest

from axiom.symbol_mapping import (
    AssetClass,
    detect_asset_class,
    from_polygon,
    timeframe_to_polygon,
    to_binance,
    to_ccxt,
    to_fs,
    to_polygon,
)


# -- detect_asset_class -------------------------------------------------------

@pytest.mark.parametrize("symbol,expected", [
    ("BTC/USDT", AssetClass.CRYPTO),
    ("BTC-USDT", AssetClass.CRYPTO),
    ("ETH/USDT:USDT", AssetClass.CRYPTO),
    ("SOL-USDC", AssetClass.CRYPTO),
    ("BTCUSDT", AssetClass.CRYPTO),
    ("X:BTCUSD", AssetClass.CRYPTO),
    ("AAPL", AssetClass.STOCK),
    ("MSFT", AssetClass.STOCK),
    ("TSLA", AssetClass.STOCK),
    ("BRK.B", AssetClass.STOCK),
    ("C:EURUSD", AssetClass.FOREX),
    ("I:SPX", AssetClass.INDEX),
    ("SPX", AssetClass.INDEX),
    ("QQQ", AssetClass.INDEX),
])
def test_detect_asset_class(symbol: str, expected: AssetClass):
    assert detect_asset_class(symbol) == expected


def test_detect_asset_class_empty():
    assert detect_asset_class("") == AssetClass.STOCK


# -- to_polygon ---------------------------------------------------------------

@pytest.mark.parametrize("symbol,expected", [
    ("BTC-USDT", "X:BTCUSD"),
    ("BTC/USDT", "X:BTCUSD"),
    ("ETH-USDC", "X:ETHUSD"),
    ("AAPL", "AAPL"),
    ("MSFT", "MSFT"),
    ("BRK-B", "BRK.B"),
    ("EUR-USD", "C:EURUSD"),
    ("SPX", "I:SPX"),
])
def test_to_polygon(symbol: str, expected: str):
    assert to_polygon(symbol) == expected


def test_to_polygon_explicit_asset_class():
    assert to_polygon("AAPL", AssetClass.STOCK) == "AAPL"
    assert to_polygon("SPX", AssetClass.INDEX) == "I:SPX"


# -- from_polygon --------------------------------------------------------------

@pytest.mark.parametrize("ticker,expected_symbol,expected_class", [
    ("X:BTCUSD", "BTC-USD", AssetClass.CRYPTO),
    ("AAPL", "AAPL", AssetClass.STOCK),
    ("BRK.B", "BRK-B", AssetClass.STOCK),
    ("C:EURUSD", "EUR-USD", AssetClass.FOREX),
    ("I:SPX", "SPX", AssetClass.INDEX),
])
def test_from_polygon(ticker: str, expected_symbol: str, expected_class: AssetClass):
    symbol, ac = from_polygon(ticker)
    assert symbol == expected_symbol
    assert ac == expected_class


# -- Roundtrip -----------------------------------------------------------------

def test_roundtrip_crypto():
    sym = "BTC-USDT"
    pg = to_polygon(sym)
    back, ac = from_polygon(pg)
    # Polygon crypto uses USD not USDT, so roundtrip maps to BTC-USD
    assert ac == AssetClass.CRYPTO
    assert "BTC" in back


def test_roundtrip_stock():
    sym = "AAPL"
    pg = to_polygon(sym)
    back, ac = from_polygon(pg)
    assert back == "AAPL"
    assert ac == AssetClass.STOCK


def test_roundtrip_forex():
    pg = to_polygon("EUR-USD")
    back, ac = from_polygon(pg)
    assert back == "EUR-USD"
    assert ac == AssetClass.FOREX


# -- to_fs, to_ccxt, to_binance -----------------------------------------------

def test_to_fs_crypto():
    assert to_fs("BTC/USDT") == "BTC-USDT"


def test_to_fs_polygon_prefixed():
    assert to_fs("X:BTCUSD") == "BTC-USD"
    assert to_fs("C:EURUSD") == "EUR-USD"
    assert to_fs("I:SPX") == "SPX"


def test_to_fs_stock():
    assert to_fs("AAPL") == "AAPL"


def test_to_ccxt():
    assert to_ccxt("BTC-USDT") == "BTC/USDT"
    assert to_ccxt("BTC/USDT") == "BTC/USDT"
    assert to_ccxt("AAPL") == "AAPL"


def test_to_binance():
    assert to_binance("BTC/USDT") == "BTCUSDT"
    assert to_binance("BTC-USDT") == "BTCUSDT"


def test_to_binance_bare_base_gets_default_quote():
    # A plain coin (no quote) must resolve to a valid USDT market, not the
    # bare base which Binance rejects with -1121 Invalid symbol.
    assert to_binance("BTC") == "BTCUSDT"
    assert to_binance("SOL") == "SOLUSDT"
    # A symbol that already carries a quote is left untouched.
    assert to_binance("ETHBTC") == "ETHBTC"
    assert to_binance("BTCUSDT") == "BTCUSDT"


# -- timeframe_to_polygon -----------------------------------------------------

@pytest.mark.parametrize("tf,expected_mult,expected_span", [
    ("1m", 1, "minute"),
    ("5m", 5, "minute"),
    ("15m", 15, "minute"),
    ("1h", 1, "hour"),
    ("4h", 4, "hour"),
    ("1d", 1, "day"),
    ("1w", 1, "week"),
])
def test_timeframe_to_polygon(tf: str, expected_mult: int, expected_span: str):
    mult, span = timeframe_to_polygon(tf)
    assert mult == expected_mult
    assert span == expected_span


def test_timeframe_to_polygon_invalid():
    with pytest.raises(ValueError):
        timeframe_to_polygon("invalid")
