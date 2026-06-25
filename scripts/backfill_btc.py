#!/usr/bin/env python3
"""Backfill BTC-USDT 1m and 5m data from Binance via CCXT.

Writes Parquet files directly to data/ohlcv/BTC-USDT/ in the same format
the Axiom BarStore uses (columns: timestamp, open, high, low, close, volume;
zstd compression). Merges with any existing data.

Run from the Axiom root: python scripts/backfill_btc.py
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ohlcv" / "BTC-USDT"
EXCHANGE_ID = "binance"
SYMBOL = "BTC/USDT"

# Binance BTC/USDT listing date
EARLIEST = datetime(2017, 8, 17, 4, 0, tzinfo=timezone.utc)

TIMEFRAMES = {
    "1m": {"chunk_limit": 1000, "ms_per_bar": 60_000},
    "5m": {"chunk_limit": 1000, "ms_per_bar": 300_000},
}


def load_existing(tf: str) -> pd.DataFrame | None:
    path = DATA_DIR / f"{tf}.parquet"
    if not path.exists():
        return None
    table = pq.read_table(path)
    df = table.to_pandas()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def save_parquet(df: pd.DataFrame, tf: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{tf}.parquet"
    tmp_path = path.with_suffix(".parquet.tmp")

    # Ensure columns match BarStore format
    out = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    table = pa.Table.from_pandas(out, preserve_index=False)

    pq.write_table(table, tmp_path, compression="zstd")
    os.replace(str(tmp_path), str(path))
    print(f"  Saved {path.name}: {len(out):,} rows")


def fetch_all(exchange: ccxt.Exchange, tf: str, cfg: dict) -> None:
    chunk_limit = cfg["chunk_limit"]
    ms_per_bar = cfg["ms_per_bar"]

    existing = load_existing(tf)
    if existing is not None and len(existing) > 5000:
        # We have substantial data — find the earliest timestamp and fetch before it
        earliest_existing = existing["timestamp"].min()
        print(f"\n[{tf}] Existing data: {len(existing):,} rows, "
              f"{earliest_existing} → {existing['timestamp'].max()}")
        # Fetch from EARLIEST up to earliest_existing
        since_ms = int(EARLIEST.timestamp() * 1000)
        until_ms = int(earliest_existing.timestamp() * 1000)
        if until_ms - since_ms < ms_per_bar:
            print(f"  No gap to fill for {tf}")
            return
        print(f"  Filling gap: {EARLIEST} → {earliest_existing}")
    else:
        print(f"\n[{tf}] Starting full fetch from {EARLIEST}")
        existing = None
        since_ms = int(EARLIEST.timestamp() * 1000)
        until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    all_chunks: list[pd.DataFrame] = []
    cursor_ms = since_ms
    total_rows = 0
    chunk_num = 0

    while cursor_ms < until_ms:
        for attempt in range(5):
            try:
                ohlcv = exchange.fetch_ohlcv(
                    SYMBOL, tf, since=cursor_ms, limit=chunk_limit
                )
                break
            except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.RequestTimeout) as e:
                delay = min(60, 2 ** attempt * 2)
                print(f"  Retry {attempt+1}/5 ({e.__class__.__name__}), waiting {delay}s...")
                time.sleep(delay)
        else:
            print(f"  FAILED after 5 retries at cursor={cursor_ms}, stopping.")
            break

        if not ohlcv:
            break

        chunk_num += 1
        rows = len(ohlcv)
        total_rows += rows

        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        all_chunks.append(df)

        last_ts = ohlcv[-1][0]
        cursor_ms = last_ts + ms_per_bar

        if chunk_num % 50 == 0:
            elapsed_pct = (cursor_ms - since_ms) / max(1, until_ms - since_ms) * 100
            ts_str = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"  Chunk {chunk_num}: {total_rows:>10,} rows | up to {ts_str} | {elapsed_pct:.1f}%")

        if rows < chunk_limit:
            break

        # Respect rate limits
        time.sleep(exchange.rateLimit / 1000)

    if not all_chunks:
        print(f"  No new data fetched for {tf}")
        return

    new_df = pd.concat(all_chunks, ignore_index=True)
    print(f"  Fetched {len(new_df):,} new rows for {tf}")

    # Merge with existing
    if existing is not None and len(existing) > 0:
        combined = pd.concat([new_df, existing], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        print(f"  Merged: {len(combined):,} total rows")
    else:
        combined = new_df.sort_values("timestamp").reset_index(drop=True)

    save_parquet(combined, tf)


def main():
    print(f"Initializing {EXCHANGE_ID}...")
    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })

    for tf, cfg in TIMEFRAMES.items():
        fetch_all(exchange, tf, cfg)

    print("\nDone! Restart the app or visit the data page to see updated catalog.")


if __name__ == "__main__":
    main()
