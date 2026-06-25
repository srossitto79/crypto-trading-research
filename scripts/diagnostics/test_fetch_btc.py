import json
import time
from datetime import datetime, timezone
import pandas as pd
from axiom.data import fetch_ohlcv_chunked, load_parquet

def test_fetch():
    # Nov 1, 2025 is 1730419200000 ms
    since_ms = 1730419200000 
    until_ms = 1730505600000
    
    try:
        print(f"Fetching BTC-USDT for Nov 1, 2025...")
        res = fetch_ohlcv_chunked(
            "BTC-USDT", "1h", 
            since_ms=since_ms, 
            until_ms=until_ms,
            exchange_id="binance"
        )
        print("Fetch result summary:")
        print(json.dumps({k: v for k, v in res.items() if k != 'data'}, indent=2))
        
        df = load_parquet("BTC-USDT", "1h")
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        nov_1 = df[(df['timestamp'] >= '2025-11-01T00:00:00Z') & (df['timestamp'] < '2025-11-02T00:00:00Z')]
        print("\nFetched/Updated data for Nov 1, 2025:")
        print(nov_1)
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_fetch()
