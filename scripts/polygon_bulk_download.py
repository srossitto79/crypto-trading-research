"""Bulk download historical stock OHLCV data from Polygon.io.

Usage:
    python scripts/polygon_bulk_download.py

Downloads 10 tech-heavy tickers × 4 timeframes (15m, 1h, 4h, 1d) from 2015-01-01.
Starter tier rate limit: 15 calls/min. Estimated runtime: 60-90 minutes.
Safe to interrupt and resume — existing data is merged/deduped on restart.
"""

import logging
import sys
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polygon_bulk")

TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "CRM"]
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
CALLS_PER_MINUTE = 15  # Starter tier allows 100/min; stay conservative


def main():
    from axiom.data import _fetch_ohlcv_polygon, load_parquet
    from axiom.polygon_client import PolygonClient

    # Verify API key works before starting
    try:
        client = PolygonClient(calls_per_minute=CALLS_PER_MINUTE)
        client.close()
        log.info("Polygon API key OK")
    except Exception as e:
        log.error("Polygon API key check failed: %s", e)
        sys.exit(1)

    # Monkey-patch the default rate limit for this session
    _original_init = PolygonClient.__init__

    def _patched_init(self, api_key=None, calls_per_minute=CALLS_PER_MINUTE):
        _original_init(self, api_key=api_key, calls_per_minute=calls_per_minute)

    PolygonClient.__init__ = _patched_init

    total_jobs = len(TICKERS) * len(TIMEFRAMES)
    results = []
    failures = []
    job_num = 0

    log.info("Starting bulk download: %d tickers × %d timeframes = %d jobs", len(TICKERS), len(TIMEFRAMES), total_jobs)
    log.info("Tickers: %s", ", ".join(TICKERS))
    log.info("Timeframes: %s", ", ".join(TIMEFRAMES))
    log.info("Rate limit: %d calls/min", CALLS_PER_MINUTE)
    log.info("")

    t_start = time.time()

    for ticker in TICKERS:
        for tf in TIMEFRAMES:
            job_num += 1
            prefix = f"[{job_num}/{total_jobs}] {ticker} {tf}"

            try:
                # Check existing data
                existing = load_parquet(ticker, tf)
                existing_rows = len(existing) if existing is not None else 0

                log.info("%s: fetching (existing: %s rows)...", prefix, f"{existing_rows:,}" if existing_rows else "none")

                result = _fetch_ohlcv_polygon(
                    symbol=ticker,
                    timeframe=tf,
                    all_available=True,
                )

                new_rows = result.get("bars_new", 0)
                total_rows = result.get("total_bars", 0)
                date_from = result.get("from", "?")
                date_to = result.get("to", "?")

                log.info("%s: +%s new rows (%s total) | %s → %s", prefix, f"{new_rows:,}", f"{total_rows:,}", date_from, date_to)
                results.append({
                    "ticker": ticker,
                    "timeframe": tf,
                    "new_rows": new_rows,
                    "total_rows": total_rows,
                    "status": "ok",
                })

            except Exception as e:
                log.error("%s: FAILED — %s", prefix, e)
                failures.append({"ticker": ticker, "timeframe": tf, "error": str(e)})
                results.append({
                    "ticker": ticker,
                    "timeframe": tf,
                    "new_rows": 0,
                    "total_rows": 0,
                    "status": "FAILED",
                })

    elapsed = time.time() - t_start
    elapsed_min = elapsed / 60

    # Summary
    log.info("")
    log.info("=" * 70)
    log.info("DOWNLOAD COMPLETE — %.1f minutes", elapsed_min)
    log.info("=" * 70)
    log.info("")
    log.info("%-8s %6s %6s %6s %6s", "Ticker", "15m", "1h", "4h", "1d")
    log.info("-" * 40)

    for ticker in TICKERS:
        row = {}
        for r in results:
            if r["ticker"] == ticker:
                if r["status"] == "ok":
                    row[r["timeframe"]] = f"{r['total_rows']:,}"
                else:
                    row[r["timeframe"]] = "FAIL"
        log.info("%-8s %6s %6s %6s %6s",
                 ticker,
                 row.get("15m", "-"),
                 row.get("1h", "-"),
                 row.get("4h", "-"),
                 row.get("1d", "-"))

    if failures:
        log.info("")
        log.warning("%d failures:", len(failures))
        for f in failures:
            log.warning("  %s %s: %s", f["ticker"], f["timeframe"], f["error"])
    else:
        log.info("")
        log.info("All %d jobs completed successfully.", total_jobs)


if __name__ == "__main__":
    main()
