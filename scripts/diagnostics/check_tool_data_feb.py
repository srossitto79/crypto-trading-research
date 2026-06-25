
import json
import pandas as pd
from axiom.data import dataset_ohlcv

def check():
    # We want Feb 2026. Mar 1 is around row 71438.
    # Feb has 28 days * 24 = 672 rows.
    # So let's take 1000 rows from the end and find Feb.
    res = dataset_ohlcv("BTC-USDT", "1h", limit=1000)
    df = pd.DataFrame(res['data'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    
    feb = df[(df['timestamp'] >= '2026-02-01T00:00:00Z') & (df['timestamp'] < '2026-02-02T00:00:00Z')]
    if feb.empty:
        print("No Feb data in last 1000 rows")
    else:
        print("Feb 1, 2026 prices from dataset_ohlcv:")
        print(feb.head(5))

if __name__ == "__main__":
    check()
