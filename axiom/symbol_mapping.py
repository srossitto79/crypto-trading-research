"""Universal symbol mapping for multi-asset support.

Maps between Axiom canonical format and provider-specific formats
for crypto, stocks, forex, and indices.
"""

from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    STOCK = "stock"
    FOREX = "forex"
    INDEX = "index"


# Polygon prefixes for non-stock asset classes
_POLYGON_CRYPTO_PREFIX = "X:"
_POLYGON_FOREX_PREFIX = "C:"
_POLYGON_INDEX_PREFIX = "I:"

# Common crypto quote currencies
_CRYPTO_QUOTES = {"USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "BNB", "EUR", "TUSD", "DAI"}

# Fiat currencies (ISO 4217) — used to distinguish forex from crypto
_FIAT_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD",
    "SEK", "NOK", "DKK", "SGD", "HKD", "MXN", "ZAR", "TRY",
    "PLN", "CZK", "HUF", "ILS", "THB", "INR", "BRL", "KRW",
    "CNY", "TWD", "RUB", "PHP", "IDR", "MYR", "CLP", "COP",
}

# Known crypto base currencies (to disambiguate from forex)
_CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOT", "AVAX",
    "DOGE", "SHIB", "MATIC", "LINK", "UNI", "ATOM", "LTC",
    "FIL", "APT", "ARB", "OP", "SUI", "SEI", "TIA", "NEAR",
    "PEPE", "WIF", "JUP", "RENDER", "FET", "INJ", "TRX",
}

# Known index tickers (expand as needed)
_INDEX_TICKERS = {
    "SPX", "NDX", "DJI", "VIX", "RUT", "GSPC", "IXIC", "NYA",
    "SPY", "QQQ", "DIA", "IWM",  # ETFs that track indices
}


def detect_asset_class(symbol: str) -> AssetClass:
    """Infer asset class from symbol format.

    Rules:
    - Contains '/' or '-' with a known crypto quote → CRYPTO
    - Starts with 'C:' → FOREX
    - Starts with 'I:' → INDEX
    - In known index set → INDEX
    - Otherwise → STOCK
    """
    s = str(symbol or "").strip().upper()
    if not s:
        return AssetClass.STOCK

    # Polygon-prefixed formats
    if s.startswith(_POLYGON_CRYPTO_PREFIX):
        return AssetClass.CRYPTO
    if s.startswith(_POLYGON_FOREX_PREFIX):
        return AssetClass.FOREX
    if s.startswith(_POLYGON_INDEX_PREFIX):
        return AssetClass.INDEX

    # Perpetual-future suffix (BTC-PERP, SOL/PERP, ETHPERP): perps only exist
    # in the crypto universe here (Hyperliquid)
    for sep in ("-", "/", ":", ""):
        suffix = f"{sep}PERP"
        if s.endswith(suffix) and len(s) > len(suffix):
            base = s[: -len(suffix)]
            if base.replace("-", "").replace("/", "").isalnum():
                return AssetClass.CRYPTO

    # Forex pairs: both halves are fiat currencies (EUR-USD, GBP/JPY)
    if "/" in s or "-" in s:
        sep = "/" if "/" in s else "-"
        parts = s.split(sep, 1)
        base_part = parts[0]
        quote_part = parts[1].split(":")[0] if len(parts) > 1 else ""
        if base_part in _FIAT_CURRENCIES and quote_part in _FIAT_CURRENCIES:
            return AssetClass.FOREX

    # Crypto pair formats: BTC/USDT, BTC-USDT, BTC/USDT:USDT
    if "/" in s:
        quote = s.split("/")[-1].split(":")[0]
        if quote in _CRYPTO_QUOTES:
            return AssetClass.CRYPTO
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 2 and parts[1] in _CRYPTO_QUOTES:
            return AssetClass.CRYPTO

    # Bare concatenated crypto: BTCUSDT
    for quote in _CRYPTO_QUOTES:
        if s.endswith(quote) and len(s) > len(quote) and s[:-len(quote)].isalpha():
            base = s[:-len(quote)]
            if len(base) >= 2 and len(base) <= 5:
                return AssetClass.CRYPTO

    # Known indices
    if s in _INDEX_TICKERS:
        return AssetClass.INDEX

    # Bare crypto bases: BTC, SOL, PEPE ... (the set exists to disambiguate
    # crypto from forex/stock; without this check every bare base fell through
    # to STOCK)
    if s in _CRYPTO_BASES:
        return AssetClass.CRYPTO

    # Forex pairs: 6-char alphabetic (EURUSD, GBPJPY)
    if len(s) == 6 and s.isalpha() and s[:3] != s[3:]:
        # Check if both halves look like currency codes
        common_currencies = {
            "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD",
            "SEK", "NOK", "DKK", "SGD", "HKD", "MXN", "ZAR", "TRY",
        }
        if s[:3] in common_currencies and s[3:] in common_currencies:
            return AssetClass.FOREX

    return AssetClass.STOCK


def to_polygon(symbol: str, asset_class: AssetClass | None = None) -> str:
    """Convert canonical symbol to Polygon.io ticker format.

    Examples:
        BTC-USDT → X:BTCUSD
        AAPL → AAPL
        EUR-USD → C:EURUSD
        SPX → I:SPX
    """
    s = str(symbol or "").strip().upper()
    if not s:
        return s

    if asset_class is None:
        asset_class = detect_asset_class(s)

    if asset_class == AssetClass.CRYPTO:
        # Normalize to base + USD for Polygon
        base = _extract_crypto_base(s)
        return f"X:{base}USD"

    if asset_class == AssetClass.FOREX:
        # Remove separators
        clean = s.replace("-", "").replace("/", "").replace("C:", "")
        return f"C:{clean}"

    if asset_class == AssetClass.INDEX:
        clean = s.replace("I:", "")
        return f"I:{clean}"

    # Stock: just the ticker, replace filesystem dashes with dots (BRK-B → BRK.B)
    return s.replace("-", ".")


def from_polygon(ticker: str) -> tuple[str, AssetClass]:
    """Convert Polygon.io ticker to canonical Axiom format + asset class.

    Examples:
        X:BTCUSD → (BTC-USD, CRYPTO)
        AAPL → (AAPL, STOCK)
        C:EURUSD → (EUR-USD, FOREX)
        I:SPX → (SPX, INDEX)
    """
    t = str(ticker or "").strip().upper()

    if t.startswith("X:"):
        pair = t[2:]
        # Crypto: strip trailing USD to get base
        if pair.endswith("USD"):
            base = pair[:-3]
            return f"{base}-USD", AssetClass.CRYPTO
        return pair, AssetClass.CRYPTO

    if t.startswith("C:"):
        pair = t[2:]
        if len(pair) == 6:
            return f"{pair[:3]}-{pair[3:]}", AssetClass.FOREX
        return pair, AssetClass.FOREX

    if t.startswith("I:"):
        return t[2:], AssetClass.INDEX

    # Stock: dots → dashes for filesystem safety (BRK.B → BRK-B)
    return t.replace(".", "-"), AssetClass.STOCK


def to_fs(symbol: str) -> str:
    """Convert any symbol to filesystem-safe format.

    BTC/USDT → BTC-USDT, AAPL → AAPL, C:EURUSD → EUR-USD
    """
    s = str(symbol or "").strip().upper()
    if not s:
        return s

    # If Polygon-prefixed, convert first
    if s.startswith(("X:", "C:", "I:")):
        canonical, _ = from_polygon(s)
        return canonical

    # Standard normalization
    return s.replace("/", "-").replace("_", "-").replace(":", "-")


def to_ccxt(symbol: str) -> str:
    """Convert to CCXT format (BTC/USDT). Only meaningful for crypto."""
    s = str(symbol or "").strip().upper()
    if not s:
        return s
    if "/" in s:
        return s
    if "-" in s:
        parts = s.split("-", 1)
        return f"{parts[0]}/{parts[1]}"
    return s


def to_binance(symbol: str) -> str:
    """Convert to Binance format (BTCUSDT). Only meaningful for crypto.

    A bare base (e.g. ``BTC``) has no quote currency, which Binance rejects with
    ``-1121 Invalid symbol``. Default it to the USDT pair so callers that pass a
    plain coin (scanner, regime detection) still resolve to a valid market.
    """
    s = str(symbol or "").strip().upper()
    s = s.replace("/", "").replace("-", "").replace("_", "").replace(":", "")
    if not s:
        return s
    # Already carries a quote (BTCUSDT, ETHBTC) → leave as-is; otherwise it's a
    # bare base (BTC, SOL) and needs the default USDT quote appended.
    has_quote = any(
        s.endswith(quote) and len(s) > len(quote) for quote in _CRYPTO_QUOTES
    )
    return s if has_quote else f"{s}USDT"


def _extract_crypto_base(symbol: str) -> str:
    """Extract base currency from any crypto symbol format."""
    s = symbol.upper().replace("X:", "")

    # BTC/USDT:USDT → BTC
    if "/" in s:
        return s.split("/")[0]
    # BTC-USDT → BTC
    if "-" in s:
        return s.split("-")[0]
    # BTCUSDT → BTC
    for quote in sorted(_CRYPTO_QUOTES, key=len, reverse=True):
        if s.endswith(quote) and len(s) > len(quote):
            return s[:-len(quote)]
    return s


def timeframe_to_polygon(timeframe: str) -> tuple[int, str]:
    """Convert Axiom timeframe to Polygon multiplier + timespan.

    Examples:
        1m → (1, "minute")
        5m → (5, "minute")
        1h → (1, "hour")
        4h → (4, "hour")
        1d → (1, "day")
        1w → (1, "week")
    """
    tf = str(timeframe or "").strip().lower()
    if not tf or len(tf) < 2:
        raise ValueError(f"Invalid timeframe: {timeframe}")

    unit = tf[-1]
    try:
        multiplier = int(tf[:-1])
    except ValueError as e:
        raise ValueError(f"Invalid timeframe: {timeframe}") from e

    unit_map = {
        "m": "minute",
        "h": "hour",
        "d": "day",
        "w": "week",
    }
    if unit not in unit_map:
        raise ValueError(f"Unsupported timeframe unit: {unit}")

    return multiplier, unit_map[unit]
