"""Historical Data Pump — Prefetches candles for simulation performance.

Prevents API rate limiting and speeds up the simulation by loading
all required historical data into a temporary SQLite database before the run starts.
"""

import logging
import asyncio
import os
import sqlite3
import pandas as pd
from datetime import datetime
from typing import List, Optional

from axiom.market_data import fetch_hyperliquid_candles

log = logging.getLogger("axiom.sim.data_pump")

# Temporary SQLite file for the data pump
_DB_PATH = "sim_candles.db"

def _get_table_name(asset: str, interval: str) -> str:
    return f"candles_{asset.lower()}_{interval.lower()}"


def _prepare_cache_frame(df: pd.DataFrame) -> pd.DataFrame:
    cached = df.copy()
    cached.index = pd.to_datetime(cached.index, utc=True, errors="coerce")
    cached = cached[~cached.index.isna()]
    cached = cached.sort_index()
    cached["t_ms"] = (cached.index.view("int64") // 1_000_000).astype("int64")
    return cached

async def prefetch_candles(
    assets: List[str], 
    start_time: datetime, 
    end_time: datetime, 
    interval: str, 
    warmup_bars: int = 300
):
    """Pre-download all candle data for the simulation period into SQLite."""
    clear_cache()
    
    # Calculate required range including warmup
    delta_map = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400
    }
    sec_per_bar = delta_map.get(interval.lower(), 3600)
    warmup_delta = pd.Timedelta(seconds=warmup_bars * sec_per_bar)
    
    fetch_start = start_time - warmup_delta
    total_bars = int((end_time - fetch_start).total_seconds() / sec_per_bar) + 1
    
    log.info("Prefetching ~%d bars for %d assets (%s) to SQLite", total_bars, len(assets), interval)
    
    conn = sqlite3.connect(_DB_PATH)
    
    try:
        for asset in assets:
            asset = asset.upper()
            curr_end = end_time
            bars_remaining = total_bars
            table_name = _get_table_name(asset, interval)
            
            rows_inserted = 0
            
            # HyperLiquid limits to ~5000 bars per request
            while bars_remaining > 0:
                chunk_size = min(bars_remaining, 5000)
                end_ms = int(curr_end.timestamp() * 1000)
                
                try:
                    df = await asyncio.to_thread(
                        fetch_hyperliquid_candles, asset, bars=chunk_size, 
                        interval=interval, end_time=end_ms
                    )
                    if df.empty:
                        break

                    cached_df = _prepare_cache_frame(df)
                    if cached_df.empty:
                        break

                    # Save to SQLite immediately
                    cached_df.to_sql(table_name, conn, if_exists="append", index=True, index_label="t")
                    rows_inserted += len(cached_df)
                    
                    # Update for next chunk (earlier in time)
                    oldest_ts = cached_df.index.min()
                    curr_end = oldest_ts - pd.Timedelta(seconds=sec_per_bar)
                    bars_remaining -= len(cached_df)
                    
                    # Courtesy sleep to avoid hitting rate limits during prefetch
                    await asyncio.sleep(0.2)
                except Exception as e:
                    log.error("Prefetch failed for %s chunk: %s", asset, e)
                    break
            
            if rows_inserted > 0:
                # Deduplicate and sort using SQL
                conn.execute(f"CREATE TABLE {table_name}_tmp AS SELECT DISTINCT * FROM {table_name} ORDER BY t ASC")
                conn.execute(f"DROP TABLE {table_name}")
                conn.execute(f"ALTER TABLE {table_name}_tmp RENAME TO {table_name}")
                conn.execute(f"CREATE INDEX idx_{table_name}_t ON {table_name}(t)")
                conn.execute(f"CREATE INDEX idx_{table_name}_t_ms ON {table_name}(t_ms)")
                conn.commit()
                log.info("Prefetched %d bars for %s", rows_inserted, asset)
    finally:
        conn.close()

def get_cached_candles(asset: str, interval: str, end_time_ms: int, bars: int) -> Optional[pd.DataFrame]:
    """Slice the pre-fetched cache for the requested virtual time from SQLite."""
    if not os.path.exists(_DB_PATH):
        return None
        
    table_name = _get_table_name(asset, interval)
    
    conn = sqlite3.connect(_DB_PATH)
    try:
        # Check if table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cursor.fetchone():
            return None

        column_rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns = {str(row[1]) for row in column_rows}
        if "t_ms" in columns:
            query = f"SELECT * FROM {table_name} WHERE t_ms <= ? ORDER BY t_ms DESC LIMIT ?"
            params = (int(end_time_ms), int(bars))
        else:
            end_ts = pd.Timestamp(end_time_ms, unit="ms", tz="UTC").isoformat().replace("T", " ")
            query = f"SELECT * FROM {table_name} WHERE t <= ? ORDER BY t DESC LIMIT ?"
            params = (end_ts, int(bars))

        df = pd.read_sql_query(query, conn, params=params, parse_dates=['t'])
        if df.empty:
            return None
            
        # Reverse to chronological order and set index
        if "t_ms" in df.columns:
            df = df.drop(columns=["t_ms"])
        df = df.iloc[::-1].set_index('t')
        return df
    except Exception as e:
        log.debug("Failed to read cached candles for %s: %s", asset, e)
        return None
    finally:
        conn.close()

def clear_cache():
    """Wipe the SQLite database."""
    if os.path.exists(_DB_PATH):
        try:
            os.remove(_DB_PATH)
            log.debug("Simulation data pump cache (SQLite) cleared")
        except OSError as e:
            log.warning("Failed to clear simulation cache: %s", e)
