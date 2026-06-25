"""Tests for asset-class-specific constants."""

from __future__ import annotations

from axiom.asset_constants import get_bars_per_year
from axiom.symbol_mapping import AssetClass


def test_crypto_bars_per_year():
    assert get_bars_per_year("1h", AssetClass.CRYPTO) == 8760
    assert get_bars_per_year("1d", AssetClass.CRYPTO) == 365
    assert get_bars_per_year("4h", AssetClass.CRYPTO) == 2190


def test_stock_bars_per_year():
    bpy_1h = get_bars_per_year("1h", AssetClass.STOCK)
    assert bpy_1h == 252 * 7  # 1764
    assert get_bars_per_year("1d", AssetClass.STOCK) == 252


def test_forex_bars_per_year():
    assert get_bars_per_year("1h", AssetClass.FOREX) == 252 * 24  # 6048
    assert get_bars_per_year("1d", AssetClass.FOREX) == 252


def test_index_matches_stock():
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert get_bars_per_year(tf, AssetClass.INDEX) == get_bars_per_year(tf, AssetClass.STOCK)


def test_default_fallback():
    # Unknown asset class falls back to crypto
    assert get_bars_per_year("1h") == 8760


def test_unknown_timeframe_fallback():
    # Unknown timeframe falls back to 8760
    assert get_bars_per_year("3d", AssetClass.STOCK) == 8760


def test_stock_lower_than_crypto():
    """Equity bars per year should be significantly less than crypto (no 24/7)."""
    crypto = get_bars_per_year("1h", AssetClass.CRYPTO)
    stock = get_bars_per_year("1h", AssetClass.STOCK)
    assert stock < crypto
    assert stock > 0
