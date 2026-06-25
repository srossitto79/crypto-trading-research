"""Market calendar utilities for multi-asset session awareness.

Provides trading hours, holidays, and session helpers for different
asset classes. Crypto is always-on (24/7). Equities and forex follow
exchange calendars.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from functools import lru_cache

from axiom.symbol_mapping import AssetClass

log = logging.getLogger("axiom.market_calendar")

# Try to import exchange_calendars; fall back to hardcoded NYSE schedule
try:
    import exchange_calendars as xcals
    _HAS_XCALS = True
except ImportError:
    xcals = None  # type: ignore[assignment]
    _HAS_XCALS = False
    log.info("exchange_calendars not installed; using hardcoded NYSE schedule fallback")


# NYSE regular trading hours (Eastern Time)
NYSE_OPEN = time(9, 30)
NYSE_CLOSE = time(16, 0)

# Forex sessions (approximate, 24h Sun-Fri)
FOREX_OPEN_DAY = 0  # Monday (Sunday 17:00 ET open)
FOREX_CLOSE_DAY = 4  # Friday (17:00 ET close)


@lru_cache(maxsize=4)
def _get_calendar(exchange: str = "XNYS"):
    """Get an exchange_calendars calendar instance (cached)."""
    if not _HAS_XCALS:
        return None
    try:
        return xcals.get_calendar(exchange)
    except Exception:
        log.warning("Failed to get calendar for %s", exchange, exc_info=True)
        return None


def is_market_open(dt: datetime, asset_class: AssetClass) -> bool:
    """Check if the market is open at a given datetime.

    Crypto is always open. Stocks/indices use NYSE calendar.
    Forex is open Sun 17:00 ET - Fri 17:00 ET.
    """
    if asset_class == AssetClass.CRYPTO:
        return True

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    if asset_class == AssetClass.FOREX:
        return _is_forex_open(dt)

    # Stocks and indices: use exchange calendar
    return _is_equity_market_open(dt)


def _is_forex_open(dt: datetime) -> bool:
    """Forex is open ~Sun 22:00 UTC to Fri 22:00 UTC (approx)."""
    weekday = dt.weekday()  # Mon=0, Sun=6
    if weekday == 5:  # Saturday — always closed
        return False
    if weekday == 6:  # Sunday — open after ~22:00 UTC
        return dt.hour >= 22
    if weekday == 4:  # Friday — close at ~22:00 UTC
        return dt.hour < 22
    return True  # Mon-Thu always open


def _is_equity_market_open(dt: datetime) -> bool:
    """Check if NYSE is open using exchange_calendars or fallback."""
    cal = _get_calendar("XNYS")
    if cal is not None:
        try:
            import pandas as pd
            ts = pd.Timestamp(dt)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return cal.is_open_on_minute(ts)
        except Exception:
            pass

    # Fallback: simple weekday check (no holiday awareness)
    return _simple_equity_check(dt)


def _simple_equity_check(dt: datetime) -> bool:
    """Basic check: Mon-Fri 9:30-16:00 ET."""
    import zoneinfo
    try:
        et = dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: UTC-5 approximation
        from datetime import timedelta
        et = dt.astimezone(timezone(timedelta(hours=-5)))

    if et.weekday() >= 5:  # Weekend
        return False

    t = et.time()
    return NYSE_OPEN <= t < NYSE_CLOSE


def get_trading_days(
    start: date,
    end: date,
    exchange: str = "XNYS",
) -> list[date]:
    """Return list of trading days between start and end (inclusive).

    Falls back to weekdays-only if exchange_calendars is unavailable.
    """
    cal = _get_calendar(exchange)
    if cal is not None:
        try:
            import pandas as pd
            sessions = cal.sessions_in_range(
                pd.Timestamp(start),
                pd.Timestamp(end),
            )
            return [s.date() for s in sessions]
        except Exception:
            log.warning("Calendar lookup failed, using weekday fallback", exc_info=True)

    # Fallback: all weekdays
    from datetime import timedelta
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def get_session_hours(
    d: date,
    exchange: str = "XNYS",
) -> tuple[time, time] | None:
    """Return (open_time, close_time) in ET for a given date.

    Returns None if the market is closed on that date.
    """
    cal = _get_calendar(exchange)
    if cal is not None:
        try:
            import pandas as pd
            ts = pd.Timestamp(d)
            if not cal.is_session(ts):
                return None
            open_dt = cal.session_open(ts)
            close_dt = cal.session_close(ts)
            import zoneinfo
            et = zoneinfo.ZoneInfo("America/New_York")
            return (
                open_dt.astimezone(et).time(),
                close_dt.astimezone(et).time(),
            )
        except Exception:
            pass

    # Fallback
    if d.weekday() >= 5:
        return None
    return (NYSE_OPEN, NYSE_CLOSE)


def next_market_open(dt: datetime, exchange: str = "XNYS") -> datetime:
    """Return the next market open datetime (UTC) after the given time."""
    cal = _get_calendar(exchange)
    if cal is not None:
        try:
            import pandas as pd
            ts = pd.Timestamp(dt)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            nxt = cal.next_open(ts)
            return nxt.to_pydatetime()
        except Exception:
            pass

    # Fallback: next weekday at 14:30 UTC (9:30 ET)
    from datetime import timedelta
    d = dt.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, time(14, 30), tzinfo=timezone.utc)


def trading_days_per_year(asset_class: AssetClass) -> int:
    """Approximate number of trading days per year for annualization."""
    if asset_class == AssetClass.CRYPTO:
        return 365
    if asset_class == AssetClass.FOREX:
        return 252  # ~same as equity
    return 252  # Stocks, indices
