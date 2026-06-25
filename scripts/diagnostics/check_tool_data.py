
import json
from axiom.data import dataset_ohlcv

def check():
    try:
        res = dataset_ohlcv("BTC-USDT", "1h", limit=5)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check()
