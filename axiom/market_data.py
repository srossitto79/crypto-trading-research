"""Shared market-data ingestion helpers for daemon and scanner workers."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import timezone

import pandas as pd

log = logging.getLogger("axiom.market_data")

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

INTERVAL_TO_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def _interval_to_timedelta(interval: str | None) -> pd.Timedelta | None:
    """Map a supported interval string to its bar width, or None if unknown."""
    ms = INTERVAL_TO_MS.get(str(interval or "").strip().lower())
    return pd.Timedelta(milliseconds=ms) if ms else None


def _resolve_clean_grid(index: pd.DatetimeIndex, interval: str | None):
    """Resolve the regularization grid for ``clean_ohlcv``.

    Priority: the caller's explicit interval, then pandas' inferred frequency,
    then the median bar spacing observed in the data. Never falls back to a
    hardcoded 1h grid — re-gridding a 15m/4h/1d series at 1h fabricates bars.
    Returns a freq usable by ``DataFrame.asfreq`` or None (skip re-gridding).
    """
    freq = _interval_to_timedelta(interval)
    if freq is not None:
        return freq
    if len(index) < 3:
        return None
    inferred = pd.infer_freq(index)
    if inferred:
        return inferred
    spacing = index.to_series().diff().median()
    if pd.notna(spacing) and spacing > pd.Timedelta(0):
        return spacing
    return None


def clean_ohlcv(df: pd.DataFrame, *, interval: str | None = None) -> pd.DataFrame:
    """Deterministic OHLCV cleaning pipeline.

    ``interval`` is the series' bar width (e.g. ``"15m"``, ``"4h"``). The
    regularization grid derives from it (or, failing that, from the data
    itself) — never a hardcoded 1h default, which used to re-grid non-1h
    series at 1h. Rows inserted for gaps carry OHLC continuation values for
    the wick/ATR pass but volume 0, so fabricated bars never pretend trading
    happened and are dropped by the volume filter below — only real exchange
    bars survive.
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()

    freq = _resolve_clean_grid(out.index, interval)
    if freq is not None:
        real_index = out.index
        try:
            regridded = out.asfreq(freq)
        except (ValueError, TypeError):
            regridded = None
        # Never lose real bars to the grid: if any original timestamp is
        # off-grid (asfreq would silently drop it), skip re-gridding.
        if regridded is not None and real_index.isin(regridded.index).all():
            out = regridded
            out = out.ffill(limit=3)
            gap_mask = ~out.index.isin(real_index)
            if gap_mask.any():
                out.loc[gap_mask, "volume"] = 0.0
        else:
            out = out.ffill(limit=3)
    else:
        out = out.ffill(limit=3)

    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - out["close"].shift()).abs(),
            (out["low"] - out["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_20 = tr.rolling(20, min_periods=5).mean()
    wick_cap = out["close"] + 3.0 * atr_20
    wick_floor = out["close"] - 3.0 * atr_20

    out["high"] = out["high"].clip(upper=wick_cap)
    out["low"] = out["low"].clip(lower=wick_floor)

    out["high"] = out[["open", "high", "close"]].max(axis=1)
    out["low"] = out[["open", "low", "close"]].min(axis=1)

    out = out[out["volume"] > 0]
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out


def compute_vpin(df: pd.DataFrame, bucket_size: float | None = None, n_buckets: int = 50) -> pd.Series:
    """Compute a rolling VPIN proxy using bar-level buy/sell imbalance."""
    if df is None or df.empty:
        return pd.Series(dtype=float)

    if len(df) < int(max(n_buckets, 1)):
        return pd.Series(0.0, index=df.index)

    close = pd.Series(df["close"].to_numpy())
    volume = pd.Series(df["volume"].to_numpy())

    price_change = close.diff().fillna(0.0)
    buy_volume = volume * (price_change > 0).astype(float)
    sell_volume = volume * (price_change <= 0).astype(float)

    if bucket_size is None or float(bucket_size) <= 0:
        bucket_size = float(volume.sum()) / float(max(int(n_buckets), 1))
    avg_bar_volume = float(volume.mean()) if len(volume) else 0.0
    bucket_bars = int(round(float(bucket_size) / avg_bar_volume)) if avg_bar_volume > 0 else 1
    window = int(max(n_buckets, bucket_bars, 1))
    min_periods = min(10, window)
    buy_roll = buy_volume.rolling(window, min_periods=min_periods).sum()
    sell_roll = sell_volume.rolling(window, min_periods=min_periods).sum()
    total_roll = volume.rolling(window, min_periods=min_periods).sum()

    vpin = (buy_roll - sell_roll).abs() / total_roll.replace(0, pd.NA)
    vpin = vpin.fillna(0.0).clip(lower=0.0, upper=1.0)
    return pd.Series(vpin.to_numpy(), index=df.index)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic engineered features used by scanners/strategies."""
    if df is None or df.empty:
        return df

    out = df.copy()
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr_14"] = tr.rolling(14).mean()
    atr_avg = tr.rolling(30).mean()
    out["atr_ratio"] = (out["atr_14"] / atr_avg.replace(0, pd.NA)).fillna(1.0)
    out["vpin"] = compute_vpin(df)
    vol_sma = df["volume"].rolling(20).mean()
    out["volume_sma_ratio"] = (df["volume"] / vol_sma.replace(0, pd.NA)).fillna(1.0)
    out["range_pct"] = ((df["high"] - df["low"]) / df["close"].replace(0, pd.NA)).fillna(0.0)
    return out


def post_hyperliquid_info(body: dict, *, timeout: int = 15) -> dict:
    """POST a HyperLiquid info payload and return decoded JSON."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        HYPERLIQUID_INFO_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def fetch_hyperliquid_candles(
    coin: str,
    *,
    bars: int = 300,
    interval: str = "1h",
    end_time: int | None = None,
    clean: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV candles from HyperLiquid and return a normalized dataframe."""
    normalized_coin = str(coin or "").strip().upper()
    if not normalized_coin:
        raise ValueError("coin is required")

    # Hyperliquid's candle API expects the bare perp coin (e.g. "LINK"), not the
    # full trading pair ("LINK/USDT") — passing the pair returns HTTP 500. Local
    # callers/cache key on the pair, so normalize only for the API request here.
    from axiom.symbol_mapping import _extract_crypto_base
    hl_coin = _extract_crypto_base(normalized_coin) or normalized_coin

    normalized_interval = str(interval or "1h").strip().lower()
    interval_ms = INTERVAL_TO_MS.get(normalized_interval)
    if interval_ms is None:
        raise ValueError(f"unsupported interval: {interval}")

    requested_bars = max(int(bars), 1)
    end_ms = int(end_time) if end_time else int(time.time() * 1000)
    start_ms = end_ms - (requested_bars * interval_ms)

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": hl_coin,
            "interval": normalized_interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    raw = post_hyperliquid_info(payload)
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"No candle data returned for {normalized_coin} {normalized_interval}")

    df = pd.DataFrame(raw)
    required = {"t", "o", "h", "l", "c", "v"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Candle response missing keys: {sorted(missing)}")

    df["t"] = pd.to_datetime(df["t"].astype(float), unit="ms", utc=True)
    df = df.set_index("t").sort_index()
    for column in ("o", "h", "l", "c", "v"):
        df[column] = df[column].astype(float)
        
    # Prevent lookahead bias / repainting by dropping the unclosed active candle
    reference_ts = pd.Timestamp(end_ms, unit="ms", tz="UTC") if end_time else pd.Timestamp.now("UTC")
    df = df[df.index + pd.Timedelta(interval_ms, unit="ms") <= reference_ts]
    
    normalized = df.rename(
        columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        }
    )
    normalized = normalized[["open", "high", "low", "close", "volume"]]
    
    if clean:
        # Pass the requested interval through so cleaning re-grids 15m/4h/1d
        # series on their own grid instead of pandas' inferred/1h fallback.
        normalized = clean_ohlcv(normalized, interval=normalized_interval)
    return normalized


def fetch_hyperliquid_funding_rate(coin: str) -> float | None:
    """Fetch current funding rate for one coin from HyperLiquid context payload."""
    normalized_coin = str(coin or "").strip().upper()
    if not normalized_coin:
        return None
    try:
        resp = post_hyperliquid_info({"type": "metaAndAssetCtxs"})
        if not isinstance(resp, list) or len(resp) < 2:
            return None
        meta, ctxs = resp[0], resp[1]
        universe = list((meta or {}).get("universe") or [])
        for idx, asset in enumerate(universe):
            if str((asset or {}).get("name") or "").upper() != normalized_coin:
                continue
            ctx = ctxs[idx] if idx < len(ctxs) else {}
            return float((ctx or {}).get("funding", 0.0))
    except Exception as exc:
        log.debug("Funding rate fetch failed for %s: %s", normalized_coin, exc)
    return None


def dataframe_to_ohlcv_rows(df: pd.DataFrame, *, max_rows: int = 600) -> list[dict]:
    """Convert normalized OHLCV dataframe into JSON-serializable row payloads."""
    if df is None or df.empty:
        return []
    rows: list[dict] = []
    start_idx = max(len(df) - max(int(max_rows), 1), 0)
    trimmed = df.iloc[start_idx:]
    for ts, row in trimmed.iterrows():
        if isinstance(ts, pd.Timestamp):
            iso_ts = ts.tz_convert(timezone.utc).isoformat() if ts.tzinfo else ts.tz_localize("UTC").isoformat()
        else:
            parsed = pd.Timestamp(ts, tz="UTC")
            iso_ts = parsed.isoformat()
        rows.append(
            {
                "t": iso_ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return rows


def ohlcv_rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    """Convert serialized OHLCV rows back into normalized dataframe form."""
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame = pd.DataFrame(rows)
    if "t" not in frame.columns:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame["t"] = pd.to_datetime(frame["t"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["t"]).set_index("t").sort_index()
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            frame[column] = 0.0
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame[["open", "high", "low", "close", "volume"]]
