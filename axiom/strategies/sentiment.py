"""Sentiment module — aggregates funding rates + Fear & Greed index."""

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from axiom.db import kv_get, kv_set

log = logging.getLogger("axiom.strategies.sentiment")

# Funding history cache directory
_FUNDING_CACHE_DIR = Path(__file__).parent.parent / "data" / "funding_cache"
_FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _post_hl(body: dict) -> dict:
    """Make request to HyperLiquid API."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def fetch_funding_rates() -> dict:
    """Get funding rates from metaAndAssetCtxs."""
    try:
        data = _post_hl({"type": "metaAndAssetCtxs"})
        if not data or not data[0] or not data[1]:
            return {}
        universe = data[0].get("universe", [])
        ctxs = data[1]
        result = {}
        for i, asset in enumerate(universe):
            ctx = ctxs[i]
            if ctx and "funding" in ctx:
                result[asset["name"]] = {
                    "funding": float(ctx["funding"]),
                    "openInterest": float(ctx.get("openInterest", 0)),
                }
        return result
    except Exception as e:
        log.warning("Funding fetch error: %s", e)
        return {}


def fetch_binance_funding_rate(symbol: str) -> dict:
    """Fetch current funding rate from Binance API.
    
    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT')
    
    Returns:
        dict with 'funding_rate', 'next_funding_time', 'timestamp'
    """
    try:
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol.upper()}&limit=1"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            if data:
                item = data[0]
                return {
                    "funding_rate": float(item["fundingRate"]),
                    "funding_time": int(item["fundingTime"]),
                    "timestamp": int(item["time"]),
                }
    except Exception as e:
        log.warning("Binance funding fetch error for %s: %s", symbol, e)
    return {}


def fetch_and_cache_funding_history(symbol: str, days: int = 90) -> list:
    """Fetch and cache historical funding rates from Binance.
    
    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT')
        days: Number of days of history to fetch
    
    Returns:
        List of funding rate records
    """
    cache_file = _FUNDING_CACHE_DIR / f"{symbol.upper()}_funding.json"
    
    # Check cache first
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                cached = json.load(f)
                # Return cached if recent (within 4 hours)
                if cached and (time.time() - cached[0].get("timestamp", 0) / 1000) < 14400:
                    return cached
        except Exception:
            pass
    
    # Fetch from Binance
    records = []
    try:
        # Binance funding rate API - get last 100 records (covers ~8 days)
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol.upper()}&limit=100"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            for item in data:
                records.append({
                    "funding_rate": float(item["fundingRate"]),
                    "funding_time": int(item["fundingTime"]),
                    "timestamp": int(item["time"]),
                })
        
        # Save to cache
        with open(cache_file, "w") as f:
            json.dump(records, f)
            
        log.info("Cached %d funding records for %s", len(records), symbol)
    except Exception as e:
        log.warning("Failed to fetch funding history for %s: %s", symbol, e)
    
    return records


@lru_cache(maxsize=32)
def _load_funding_records(symbol_upper: str, _cache_mtime_ns: int) -> tuple:
    """Load + sort a symbol's funding records once, memoized per file version.

    Keyed on the file's mtime so a refreshed cache reloads, while a single
    backtest run (thousands of bars) parses the JSON exactly once instead of
    per-bar. Returns a tuple of (funding_time_ms, funding_rate) ascending.
    """
    cache_file = _FUNDING_CACHE_DIR / f"{symbol_upper}_funding.json"
    try:
        with open(cache_file, "r") as f:
            records = json.load(f)
    except Exception as exc:
        log.debug("Funding cache read error for %s: %s", symbol_upper, exc)
        return ()
    out: list[tuple[int, float]] = []
    for record in records or []:
        ft = record.get("funding_time")
        fr = record.get("funding_rate")
        if ft is None or fr is None:
            continue
        try:
            out.append((int(ft), float(fr)))
        except (TypeError, ValueError):
            continue
    out.sort()
    return tuple(out)


# Funding is exchanged every 8h; allow a normal interval plus buffer before a
# missing window is treated as a real gap (-> no funding, not a carried value).
_FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000
_FUNDING_MAX_STALENESS_MS = 2 * _FUNDING_INTERVAL_MS  # 16h


def get_funding_for_backtest(symbol: str, timestamp: int) -> float:
    """Historical funding rate for a bar during backtesting — no lookahead.

    Returns the funding rate in effect AT OR BEFORE ``timestamp`` (backward
    lookup) within a staleness cap. It NEVER returns the latest (future) rate and
    NEVER fabricates synthetic funding: an unknown or gapped funding window yields
    0.0 (no funding signal), so a funding strategy simply takes no signal there
    rather than training on leaked or invented data.

    Args:
        symbol: Trading pair (e.g. 'BTC', 'ETH'); 'USDT' is appended if absent.
        timestamp: Bar open time, unix milliseconds.

    Returns:
        Funding rate as a decimal (0.0001 = 0.01%); 0.0 when genuinely unknown.
    """
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    symbol_upper = symbol.upper()

    cache_file = _FUNDING_CACHE_DIR / f"{symbol_upper}_funding.json"
    try:
        mtime_ns = cache_file.stat().st_mtime_ns
    except OSError:
        return 0.0  # no funding cache for this symbol — do NOT fabricate

    records = _load_funding_records(symbol_upper, mtime_ns)
    if not records:
        return 0.0

    # Backward lookup: most recent funding_time at or before the bar.
    best_time: int | None = None
    best_rate = 0.0
    for ft, fr in records:  # ascending
        if ft > timestamp:
            break
        best_time, best_rate = ft, fr

    if best_time is None or (timestamp - best_time) > _FUNDING_MAX_STALENESS_MS:
        return 0.0  # nothing known at/before this bar within the staleness cap
    return best_rate


def fetch_fng() -> dict:
    """Fetch Fear & Greed index."""
    try:
        req = urllib.request.Request("https://api.alternative.me/fng/")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            if data.get("data"):
                item = data["data"][0]
                return {
                    "score": int(item["value"]),
                    "label": item["value_classification"],
                    "timestamp": int(item["timestamp"]),
                }
    except Exception:
        pass
    return {"score": 50, "label": "Neutral"}


def analyze_sentiment() -> dict:
    """Compute overall sentiment score (0-100, 50=neutral)."""
    sentiments = {}

    # Load funding history from KV store
    funding_history = kv_get("funding_history", {"BTC": [], "ETH": [], "SOL": []})

    # Funding sentiment
    funding_data = fetch_funding_rates()

    for coin in ["BTC", "ETH", "SOL"]:
        rate = funding_data.get(coin, {}).get("funding", 0)
        oi = funding_data.get(coin, {}).get("openInterest", 0)

        history = funding_history.get(coin, [])
        history.append({"time": time.time(), "rate": rate})
        if len(history) > 24:
            history = history[-24:]
        funding_history[coin] = history

        if len(history) >= 4:
            avg_rate = sum(h["rate"] for h in history[-4:]) / 4
            funding_sentiment = 50 - (avg_rate * 10000)
            funding_sentiment = max(0, min(100, funding_sentiment))
        else:
            funding_sentiment = 50

        sentiments[coin] = {
            "funding_rate": rate,
            "open_interest": oi,
            "funding_sentiment": round(funding_sentiment, 1),
        }

    # Save funding history
    kv_set("funding_history", funding_history)

    # Fear & Greed
    fng = fetch_fng()

    # Composite
    composite = (
        sentiments["BTC"]["funding_sentiment"] * 0.15
        + sentiments["ETH"]["funding_sentiment"] * 0.15
        + sentiments["SOL"]["funding_sentiment"] * 0.10
        + fng["score"] * 0.40
        + 50 * 0.20
    )

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "composite": round(composite, 1),
        "fng": fng,
        "funding": sentiments,
        "interpretation": _interpret(composite),
    }

    kv_set("sentiment", result)
    return result


def _interpret(score: float) -> str:
    if score >= 65:
        return "Greed / Bullish"
    elif score >= 55:
        return "Optimistic"
    elif score >= 45:
        return "Neutral"
    elif score >= 35:
        return "Pessimistic"
    else:
        return "Fear / Bearish"
