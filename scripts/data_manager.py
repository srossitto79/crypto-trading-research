import argparse
import sys
import logging
import traceback
from pathlib import Path
from multiprocessing import cpu_count
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure Axiom is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axiom.data import fetch_ohlcv_chunked

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger("data-manager")

def download_data(ticker, timeframe, exchange, all_available=True):
    logger.info(f"Starting download for {ticker} | {timeframe} | Exchange: {exchange} | All Available: {all_available}")
    try:
        res = fetch_ohlcv_chunked(
            symbol=ticker,
            timeframe=timeframe,
            exchange_id=exchange,
            all_available=all_available,
            limit=1000 if not all_available else None
        )
        logger.info(f"SUCCESS [{ticker} | {timeframe}]: Fetched {res.get('bars_new', 0)} new bars. Total bars: {res.get('row_count', 0)}. Range: {res.get('start_ts')} -> {res.get('end_ts')}")
        return res
    except Exception as e:
        logger.error(f"FAILED [{ticker} | {timeframe}]: {e}")
        # traceback.print_exc()
        return None

def main():
    parser = argparse.ArgumentParser(description="Axiom Standalone Data Manager")
    parser.add_argument("--ticker", required=True, help="Ticker to download (e.g., BTC/USDT)")
    parser.add_argument("--timeframes", required=True, help="Comma separated timeframes (e.g., 1m,5m,15m,1h,4h,1d)")
    parser.add_argument("--exchange", default="binance", help="Exchange to download from (default: binance)")
    parser.add_argument("--sync", action="store_true", help="Run synchronously instead of multithreaded (safer for rate limits)")
    parser.add_argument("--recent", action="store_true", help="Only download the most recent 1000 bars instead of all available history")
    
    args = parser.parse_args()
    
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    ticker = args.ticker
    exchange = args.exchange
    all_available = not args.recent
    
    if args.sync:
        for tf in timeframes:
            download_data(ticker, tf, exchange, all_available)
    else:
        # For multiple timeframes, download them concurrently
        # Max workers capped conservatively to avoid getting banned by the exchange API rate limit
        workers = min(len(timeframes), 3) 
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(download_data, ticker, tf, exchange, all_available) for tf in timeframes]
            for future in as_completed(futures):
                future.result()

if __name__ == "__main__":
    main()
