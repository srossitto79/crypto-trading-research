
import json
from axiom.agents.tools_core import _tool_get_local_ohlcv

def test_tool():
    res = _tool_get_local_ohlcv("BTC-USDT", "1h", limit=5)
    print(res)

if __name__ == "__main__":
    test_tool()
