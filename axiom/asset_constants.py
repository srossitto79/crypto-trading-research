"""Asset-class-specific constants for backtesting and analytics.

Provides correct bars-per-year for Sharpe ratio annualization
across different asset classes and timeframes.
"""

from __future__ import annotations

from axiom.symbol_mapping import AssetClass

# Crypto: 24/7/365
_CRYPTO_BARS_PER_YEAR = {
    "1m": 525_960,
    "5m": 105_192,
    "15m": 35_064,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
    "1w": 52,
}

# Equities: 252 trading days, 6.5 hours RTH per day
_EQUITY_BARS_PER_YEAR = {
    "1m": 252 * 390,       # 252 * 6.5h * 60min = 98,280
    "5m": 252 * 78,        # 252 * 6.5h * 12 = 19,656
    "15m": 252 * 26,       # 252 * 6.5h * 4 = 6,552
    "1h": 252 * 7,         # 252 * ~6.5 ≈ 1,764 (round to 7 bars per day)
    "4h": 252 * 2,         # 252 * 2 bars per day = 504
    "1d": 252,
    "1w": 52,
}

# Forex: ~252 trading days, effectively 24h (Sun eve - Fri eve)
_FOREX_BARS_PER_YEAR = {
    "1m": 252 * 1440,      # 252 * 24h * 60min = 362,880
    "5m": 252 * 288,       # 252 * 24h * 12 = 72,576
    "15m": 252 * 96,       # 252 * 24h * 4 = 24,192
    "1h": 252 * 24,        # 252 * 24 = 6,048
    "4h": 252 * 6,         # 252 * 6 = 1,512
    "1d": 252,
    "1w": 52,
}

_BARS_PER_YEAR = {
    AssetClass.CRYPTO: _CRYPTO_BARS_PER_YEAR,
    AssetClass.STOCK: _EQUITY_BARS_PER_YEAR,
    AssetClass.INDEX: _EQUITY_BARS_PER_YEAR,
    AssetClass.FOREX: _FOREX_BARS_PER_YEAR,
}


def get_bars_per_year(
    timeframe: str,
    asset_class: AssetClass = AssetClass.CRYPTO,
) -> int:
    """Return the number of bars per year for annualization.

    Falls back to crypto (24/7) if asset class or timeframe unknown.
    """
    class_map = _BARS_PER_YEAR.get(asset_class, _CRYPTO_BARS_PER_YEAR)
    return class_map.get(timeframe, _CRYPTO_BARS_PER_YEAR.get(timeframe, 8760))
