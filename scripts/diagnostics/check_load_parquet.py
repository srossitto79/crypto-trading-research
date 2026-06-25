
import json
import pandas as pd
from axiom.data import load_parquet

def check():
    df = load_parquet("BTC-USDT", "1h")
    if df is None:
        print("No data")
        return
    
    # Filter for Nov 2025
    nov_2025 = df[(df.index >= '2025-11-01') & (df.index < '2025-12-01')]
    if nov_2025.empty:
        print("No data for Nov 2025")
    else:
        print("Sample data from Nov 2025:")
        print(nov_2025.head(5))

if __name__ == "__main__":
    check()
