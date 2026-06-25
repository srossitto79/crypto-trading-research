
import json
import pandas as pd
from axiom.data import load_parquet

def check():
    df = load_parquet("BTC-USDT", "1h")
    if df is None:
        print("No data")
        return
    
    # Filter for Feb 2026
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    feb_2026 = df[(df['timestamp'] >= '2026-02-01T00:00:00Z') & (df['timestamp'] < '2026-03-01T00:00:00Z')]
    if feb_2026.empty:
        print("No data for Feb 2026")
    else:
        print("Sample data from Feb 2026:")
        print(feb_2026.head(5))

if __name__ == "__main__":
    check()
