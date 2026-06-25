"""Tests for market calendar utilities."""

from __future__ import annotations

from datetime import date, datetime, timezone

from axiom.market_calendar import (
    get_trading_days,
    is_market_open,
    trading_days_per_year,
)
from axiom.symbol_mapping import AssetClass


def test_crypto_always_open():
    """Crypto markets should always be open."""
    # Saturday midnight UTC
    dt = datetime(2026, 3, 14, 0, 0, tzinfo=timezone.utc)
    assert is_market_open(dt, AssetClass.CRYPTO) is True

    # Sunday afternoon
    dt = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc)
    assert is_market_open(dt, AssetClass.CRYPTO) is True


def test_equity_closed_on_weekend():
    """Equities should be closed on weekends."""
    # Saturday
    dt = datetime(2026, 3, 14, 15, 0, tzinfo=timezone.utc)
    assert is_market_open(dt, AssetClass.STOCK) is False


def test_equity_open_on_weekday():
    """Equities should be open during RTH on a weekday."""
    # Wednesday 15:00 UTC = 10:00 AM ET (market open)
    dt = datetime(2026, 3, 18, 15, 0, tzinfo=timezone.utc)
    assert is_market_open(dt, AssetClass.STOCK) is True


def test_forex_closed_on_saturday():
    """Forex should be closed on Saturday."""
    dt = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    assert is_market_open(dt, AssetClass.FOREX) is False


def test_forex_open_weekday():
    """Forex should be open on a normal weekday."""
    dt = datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc)
    assert is_market_open(dt, AssetClass.FOREX) is True


def test_trading_days_basic():
    """Should return only weekdays (at minimum) between two dates."""
    days = get_trading_days(date(2026, 3, 9), date(2026, 3, 13))
    # March 9-13, 2026 is Mon-Fri
    assert len(days) == 5
    for d in days:
        assert d.weekday() < 5


def test_trading_days_excludes_weekend():
    days = get_trading_days(date(2026, 3, 14), date(2026, 3, 15))
    # Saturday and Sunday — should be empty
    assert len(days) == 0


def test_trading_days_per_year():
    assert trading_days_per_year(AssetClass.CRYPTO) == 365
    assert trading_days_per_year(AssetClass.STOCK) == 252
    assert trading_days_per_year(AssetClass.FOREX) == 252
