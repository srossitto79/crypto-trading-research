from axiom.data import fetch_ohlcv_chunked
print("Testing downloader...")
try:
    res = fetch_ohlcv_chunked("BTC/USDT", "1m", limit=2000)
    print("Success:", res)
except Exception as e:
    import traceback
    traceback.print_exc()
