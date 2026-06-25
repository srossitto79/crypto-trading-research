"""
Backfill all available OHLCV data for the top 5 coins across 8 timeframes.

Coins:  BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT
TFs:    1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
Source: Binance via CCXT

Downloads sequentially (one coin, one timeframe at a time) for rate-limit
safety.  Re-runnable — existing data merges automatically, so interrupting
and re-running resumes where it left off.

Usage:
    python scripts/backfill_top5.py
    python scripts/backfill_top5.py --coins BTC/USDT,ETH/USDT
    python scripts/backfill_top5.py --timeframes 1h,4h,1d
"""

import argparse
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axiom.data import fetch_ohlcv_chunked

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger("backfill-top5")

DEFAULT_COINS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
]

DEFAULT_TIMEFRAMES = [
    "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w",
]

EXCHANGE = "binance"


def backfill_one(symbol: str, timeframe: str) -> dict | None:
    logger.info(f">>> {symbol} {timeframe} — starting download")
    t0 = time.time()
    try:
        result = fetch_ohlcv_chunked(
            symbol=symbol,
            timeframe=timeframe,
            exchange_id=EXCHANGE,
            all_available=True,
        )
        elapsed = time.time() - t0
        logger.info(
            f"<<< {symbol} {timeframe} — "
            f"{result.get('bars_new', 0)} new bars, "
            f"{result.get('row_count', 0)} total, "
            f"range {result.get('start_ts', '?')} -> {result.get('end_ts', '?')}, "
            f"{elapsed:.1f}s"
        )
        return result
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"!!! {symbol} {timeframe} — FAILED after {elapsed:.1f}s: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Backfill OHLCV data for top coins")
    parser.add_argument(
        "--coins",
        default=",".join(DEFAULT_COINS),
        help="Comma-separated coin list (default: top 5)",
    )
    parser.add_argument(
        "--timeframes",
        default=",".join(DEFAULT_TIMEFRAMES),
        help="Comma-separated timeframes (default: all 8)",
    )
    args = parser.parse_args()

    coins = [c.strip() for c in args.coins.split(",") if c.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    total = len(coins) * len(timeframes)
    logger.info(f"Backfill plan: {len(coins)} coins × {len(timeframes)} timeframes = {total} datasets")
    logger.info(f"Coins: {coins}")
    logger.info(f"Timeframes: {timeframes}")
    logger.info(f"Exchange: {EXCHANGE}")
    print()

    results = []
    done = 0

    for coin in coins:
        for tf in timeframes:
            done += 1
            logger.info(f"[{done}/{total}] {coin} {tf}")
            result = backfill_one(coin, tf)
            results.append((coin, tf, result))
        print()

    # Summary
    print("\n" + "=" * 80)
    print(f"{'COIN':<14} {'TF':<6} {'NEW':>10} {'TOTAL':>10} {'RANGE':<45} {'STATUS'}")
    print("-" * 80)
    ok_count = 0
    for coin, tf, r in results:
        if r:
            ok_count += 1
            start = r.get("start_ts", "?")
            end = r.get("end_ts", "?")
            print(
                f"{coin:<14} {tf:<6} {r.get('bars_new', 0):>10,} "
                f"{r.get('row_count', 0):>10,} "
                f"{start} -> {end:<20} OK"
            )
        else:
            print(f"{coin:<14} {tf:<6} {'—':>10} {'—':>10} {'':45} FAILED")
    print("=" * 80)
    print(f"Completed: {ok_count}/{total}")


if __name__ == "__main__":
    main()
