import json
import time
import pandas as pd
from axiom.data import get_exchange, _rows_to_frame
from axiom.market_data import fetch_hyperliquid_candles

def compare():
    symbol = "BTC/USDT"
    since_ms = 1730419200000 
    
    try:
        print("Fetching from Binance via CCXT...")
        binance = get_exchange("binance")
        rows = binance.fetch_ohlcv(symbol, timeframe="1h", since=since_ms, limit=5)
        df_binance = _rows_to_frame(rows)
        print("Binance Nov 1, 2025:")
        print(df_binance.head(5))
    except Exception as e:
        print(f"Binance fetch error: {e}")
    
    try:
        print("\nFetching from HyperLiquid via API...")
        # Since HyperLiquid fetch_hyperliquid_candles takes end_time and bars
        # Nov 1, 2025 00:00 is since_ms. Let's get 5 bars starting from it.
        # So end_time = since_ms + 5 hours.
        df_hl = fetch_hyperliquid_candles("BTC", bars=6, interval="1h", end_time=since_ms + 6*3600*1000)
        print("HyperLiquid Nov 1, 2025 (last 5 rows):")
        print(df_hl.tail(5))
    except Exception as e:
        print(f"HyperLiquid fetch error: {e}")

if __name__ == "__main__":
    compare()
