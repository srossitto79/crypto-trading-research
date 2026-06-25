
import json
import pandas as pd
from axiom.agents.tools_core import _tool_get_local_ohlcv

def test_tool():
    res_str = _tool_get_local_ohlcv("BTC-USDT", "1h", limit=5000)
    res = json.loads(res_str)
    df = pd.DataFrame(res['bars'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    
    nov_1 = df[(df['timestamp'] >= '2025-11-01T00:00:00Z') & (df['timestamp'] < '2025-11-02T00:00:00Z')]
    if nov_1.empty:
        print(f"No Nov data in last 5000 rows. Earliest: {df['timestamp'].iloc[0]}")
    else:
        print("Nov 1, 2025 prices from _tool_get_local_ohlcv:")
        print(nov_1.head(5))

if __name__ == "__main__":
    test_tool()
