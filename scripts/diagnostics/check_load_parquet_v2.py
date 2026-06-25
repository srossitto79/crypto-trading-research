
import json
import pandas as pd
from axiom.data import load_parquet

def check():
    df = load_parquet("BTC-USDT", "1h")
    if df is None:
        print("No data")
        return
    
    # Filter for Nov 2025
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    nov_2025 = df[(df['timestamp'] >= '2025-11-01T00:00:00Z') & (df['timestamp'] < '2025-12-01T00:00:00Z')]
    if nov_2025.empty:
        print("No data for Nov 2025")
    else:
        print("Sample data from Nov 2025:")
        print(nov_2025.head(5))

if __name__ == "__main__":
    check()
