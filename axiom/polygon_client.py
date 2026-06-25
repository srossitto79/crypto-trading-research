"""Polygon.io REST API client for multi-asset OHLCV data.

Uses direct httpx calls (no SDK) to minimize supply chain risk.
Includes rate limiting, schema validation, and retry logic.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import httpx
import pandas as pd

from axiom.config import get_polygon_api_key, redact_api_key
from axiom.rate_limiter import RateLimiter
from axiom.symbol_mapping import AssetClass, detect_asset_class, timeframe_to_polygon, to_polygon

log = logging.getLogger("axiom.polygon")

_BASE_URL = "https://api.polygon.io"
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0


class PolygonError(Exception):
    """Base exception for Polygon API errors."""


class PolygonAuthError(PolygonError):
    """Invalid or missing API key."""


class PolygonRateLimitError(PolygonError):
    """Rate limit exceeded."""


class PolygonClient:
    """REST client for Polygon.io aggregate bars and reference data.

    Args:
        api_key: Polygon API key. If None, reads from config/env.
        calls_per_minute: Rate limit ceiling (default 4 = 80% of free tier).
    """

    def __init__(
        self,
        api_key: str | None = None,
        calls_per_minute: int = 4,
    ):
        self._api_key = api_key or get_polygon_api_key()
        if not self._api_key:
            raise PolygonAuthError(
                "Polygon API key not configured. Set POLYGON_API_KEY env var "
                "or add it in Settings > API Keys."
            )
        self._rate_limiter = RateLimiter(calls_per_minute)
        self._client = httpx.Client(
            base_url=_BASE_URL,
            timeout=_DEFAULT_TIMEOUT,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        log.info("PolygonClient initialized (key: %s)", redact_api_key(self._api_key))

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # Core API methods
    # ------------------------------------------------------------------

    def fetch_aggs(
        self,
        symbol: str,
        timeframe: str,
        from_date: str | date,
        to_date: str | date,
        *,
        adjusted: bool = True,
        limit: int = 50_000,
    ) -> pd.DataFrame:
        """Fetch OHLCV aggregate bars from Polygon.

        Args:
            symbol: Axiom canonical symbol (AAPL, BTC-USDT, EUR-USD, etc.)
            timeframe: Axiom timeframe (1m, 5m, 1h, 4h, 1d, etc.)
            from_date: Start date (YYYY-MM-DD string or date object)
            to_date: End date (YYYY-MM-DD string or date object)
            adjusted: Whether to use split-adjusted prices (default True)
            limit: Max results per request (Polygon max: 50000)

        Returns:
            DataFrame with columns: [timestamp, open, high, low, close, volume]
        """
        asset_class = detect_asset_class(symbol)
        ticker = to_polygon(symbol, asset_class)
        multiplier, timespan = timeframe_to_polygon(timeframe)

        from_str = str(from_date)
        to_str = str(to_date)

        all_results: list[dict] = []
        url = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_str}/{to_str}"
        params: dict[str, Any] = {
            "adjusted": str(adjusted).lower(),
            "sort": "asc",
            "limit": min(limit, 50_000),
        }

        while url:
            data = self._request("GET", url, params=params)
            results = data.get("results") or []
            all_results.extend(results)

            # Pagination: Polygon returns next_url for more results
            next_url = data.get("next_url")
            if next_url and results:
                # next_url is a full URL; extract path
                url = next_url.replace(_BASE_URL, "")
                params = {}  # next_url includes all params
            else:
                url = None  # type: ignore[assignment]

        if not all_results:
            log.info("No data returned for %s %s (%s → %s)", symbol, timeframe, from_str, to_str)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        return self._results_to_dataframe(all_results, symbol)

    def fetch_tickers(
        self,
        asset_class: AssetClass | None = None,
        search: str = "",
        limit: int = 100,
        active: bool = True,
    ) -> list[dict[str, Any]]:
        """Search for available tickers.

        Args:
            asset_class: Filter by asset class (maps to Polygon type)
            search: Search string for ticker or company name
            limit: Max results
            active: Only active tickers

        Returns:
            List of ticker info dicts with keys: ticker, name, type, locale, currency
        """
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "limit": min(limit, 1000),
            "order": "asc",
            "sort": "ticker",
        }

        # Map asset class to Polygon type filter
        if asset_class == AssetClass.STOCK:
            params["type"] = "CS"  # Common Stock
            params["market"] = "stocks"
        elif asset_class == AssetClass.CRYPTO:
            params["market"] = "crypto"
        elif asset_class == AssetClass.FOREX:
            params["market"] = "fx"
        elif asset_class == AssetClass.INDEX:
            params["type"] = "INDEX"
            params["market"] = "indices"

        if search:
            params["search"] = search

        data = self._request("GET", "/v3/reference/tickers", params=params)
        results = data.get("results") or []

        return [
            {
                "ticker": r.get("ticker", ""),
                "name": r.get("name", ""),
                "type": r.get("type", ""),
                "locale": r.get("locale", ""),
                "currency": r.get("currency_name", ""),
                "market": r.get("market", ""),
                "active": r.get("active", True),
            }
            for r in results
        ]

    def get_market_status(self) -> dict[str, Any]:
        """Get current market status (open/closed for each market)."""
        return self._request("GET", "/v1/marketstatus/now")

    def validate_key(self) -> bool:
        """Test if the API key is valid by making a lightweight call."""
        try:
            self._request("GET", "/v1/marketstatus/now")
            return True
        except PolygonAuthError:
            return False
        except Exception:
            # Network errors etc. don't mean the key is invalid
            return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a rate-limited, retrying HTTP request to Polygon."""
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            self._rate_limiter.acquire()
            try:
                resp = self._client.request(method, path, params=params)

                if resp.status_code == 403:
                    raise PolygonAuthError("Invalid Polygon API key")
                if resp.status_code == 429:
                    wait = _RETRY_BACKOFF_BASE ** (attempt + 1)
                    log.warning("Polygon rate limit hit, waiting %.1fs", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = _RETRY_BACKOFF_BASE ** attempt
                    log.warning(
                        "Polygon server error %d, retry %d/%d in %.1fs",
                        resp.status_code, attempt + 1, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    last_error = PolygonError(f"Server error: {resp.status_code}")
                    continue

                resp.raise_for_status()
                return resp.json()

            except (PolygonAuthError, PolygonRateLimitError):
                raise
            except httpx.HTTPStatusError as e:
                last_error = PolygonError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF_BASE ** attempt)
            except httpx.HTTPError as e:
                last_error = PolygonError(f"Network error: {e}")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF_BASE ** attempt)

        raise last_error or PolygonError("Request failed after retries")

    def _results_to_dataframe(
        self,
        results: list[dict],
        symbol: str,
    ) -> pd.DataFrame:
        """Convert Polygon aggs results to normalized OHLCV DataFrame.

        Validates schema: rejects rows with missing prices or negative volume.
        """
        records = []
        for r in results:
            ts = r.get("t")
            o = r.get("o")
            h = r.get("h")
            l = r.get("l")
            c = r.get("c")
            v = r.get("v", 0)

            # Schema validation: skip rows with missing critical fields
            if ts is None or o is None or h is None or l is None or c is None:
                log.debug("Skipping row with missing fields for %s: %s", symbol, r)
                continue

            # Reject negative/zero prices
            try:
                o_f, h_f, l_f, c_f, v_f = float(o), float(h), float(l), float(c), float(v)
            except (TypeError, ValueError):
                log.debug("Skipping row with non-numeric values for %s: %s", symbol, r)
                continue

            if o_f <= 0 or h_f <= 0 or l_f <= 0 or c_f <= 0:
                log.debug("Skipping row with zero/negative prices for %s: %s", symbol, r)
                continue

            if v_f < 0:
                v_f = 0  # Treat negative volume as zero

            records.append({
                "timestamp": pd.Timestamp(ts, unit="ms", tz="UTC"),
                "open": o_f,
                "high": h_f,
                "low": l_f,
                "close": c_f,
                "volume": v_f,
            })

        if not records:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(records)
        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
