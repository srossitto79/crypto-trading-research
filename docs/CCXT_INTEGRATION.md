# CCXT Integration Guide

## Overview

The `CCXTExchange` adapter connects Forven to 100+ cryptocurrency exchanges supported by the [CCXT library](https://github.com/ccxt/ccxt). This enables:

- **Multi-exchange support**: Binance, Kraken, Coinbase, Kucoin, Bybit, OKX, Huobi, Gate.io, and 95+ others
- **Seamless swapping**: Change exchanges without modifying code
- **Testing**: Use MockExchange for paper trading, CCXTExchange for live

## Installation

```bash
pip install ccxt
```

## Quick Start

### Using Binance Spot Trading

```python
from forven.exchange.ccxt_adapter import CCXTExchange
from forven.exchange.sync_wrapper import SyncExchange

# Create exchange instance
exchange = CCXTExchange(
    exchange_id='binance',
    api_key='YOUR_API_KEY',
    api_secret='YOUR_API_SECRET',
)

# Wrap for sync contexts (FastAPI routes, CLI, etc)
sync_exchange = SyncExchange(exchange)

# Now you can use it like any other exchange
account_value = sync_exchange.get_account_value()
positions = sync_exchange.get_positions()
```

### Using Binance Testnet

```python
exchange = CCXTExchange(
    exchange_id='binance',
    api_key='YOUR_TESTNET_KEY',
    api_secret='YOUR_TESTNET_SECRET',
    testnet=True,
)
```

### Using Different Exchanges

```python
# Kraken
kraken = CCXTExchange(
    exchange_id='kraken',
    api_key='...',
    api_secret='...',
)

# Coinbase
coinbase = CCXTExchange(
    exchange_id='coinbase',
    api_key='...',
    api_secret='...',
)

# Kucoin (supports more features)
kucoin = CCXTExchange(
    exchange_id='kucoin',
    api_key='...',
    api_secret='...',
    passphrase='YOUR_PASSPHRASE',  # Kucoin specific
)
```

## Runtime Exchange Swapping

The `forven.exchange.hyperliquid` module provides a singleton pattern for swapping exchanges at runtime:

```python
from forven.exchange.hyperliquid import get_exchange, set_exchange
from forven.exchange.ccxt_adapter import CCXTExchange
from forven.exchange.mock import MockExchange

# Swap to Binance for live trading
binance = CCXTExchange(
    exchange_id='binance',
    api_key='...',
    api_secret='...',
)
set_exchange(binance)

# Now all code uses Binance
exchange = get_exchange()
await exchange.market_order('BTC/USDT', 'buy', 1.0)

# Swap back to mock for testing
mock = MockExchange()
set_exchange(mock)
```

## Supported Exchanges

CCXT supports 100+ exchanges. Here are the most popular:

| Exchange | Exchange ID | Spot | Futures | Testnet | Notes |
|----------|-------------|------|---------|---------|-------|
| Binance | `binance` | ✅ | ✅ | ✅ | Most liquid, all features |
| Kraken | `kraken` | ✅ | ✅ | ✅ | US-friendly, good API |
| Coinbase | `coinbase` | ✅ | ❌ | ❌ | Regulated, limited leverage |
| Kucoin | `kucoin` | ✅ | ✅ | ✅ | Good features, volume |
| OKX | `okx` | ✅ | ✅ | ✅ | Advanced features |
| Bybit | `bybit` | ✅ | ✅ | ✅ | Derivatives focused |
| Gate.io | `gateio` | ✅ | ✅ | ✅ | Good volume |
| Huobi | `huobi` | ✅ | ✅ | ❌ | Large volume |
| MEXC | `mexc` | ✅ | ✅ | ✅ | Spot + futures |
| Bitget | `bitget` | ✅ | ✅ | ✅ | Copy trading |

For a complete list, see [CCXT Supported Exchanges](https://docs.ccxt.com/manual/docs/exchange-markets).

## Configuration Patterns

### Pattern 1: Environment Variables

```python
import os
from forven.exchange.ccxt_adapter import CCXTExchange

exchange = CCXTExchange(
    exchange_id=os.getenv('EXCHANGE_ID', 'binance'),
    api_key=os.getenv('EXCHANGE_API_KEY'),
    api_secret=os.getenv('EXCHANGE_API_SECRET'),
)
```

Add to `.env`:
```
EXCHANGE_ID=binance
EXCHANGE_API_KEY=your_key_here
EXCHANGE_API_SECRET=your_secret_here
```

### Pattern 2: Settings-Based Configuration

In `forven/config.py`:
```python
from pydantic import BaseSettings

class Settings(BaseSettings):
    exchange_id: str = 'binance'
    exchange_api_key: str
    exchange_api_secret: str
    use_testnet: bool = False

    class Config:
        env_file = '.env'

settings = Settings()
```

Then in exchange setup:
```python
from forven.config import settings
from forven.exchange.ccxt_adapter import CCXTExchange

exchange = CCXTExchange(
    exchange_id=settings.exchange_id,
    api_key=settings.exchange_api_key,
    api_secret=settings.exchange_api_secret,
    testnet=settings.use_testnet,
)
```

### Pattern 3: Runtime Configuration UI

In a settings endpoint:
```python
from fastapi import APIRouter, Depends
from forven.exchange.ccxt_adapter import CCXTExchange
from forven.exchange.hyperliquid import set_exchange

router = APIRouter()

@router.post("/settings/exchange")
async def configure_exchange(
    exchange_id: str,
    api_key: str,
    api_secret: str,
    testnet: bool = False,
):
    """Configure exchange at runtime."""
    try:
        exchange = CCXTExchange(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        await exchange.health_check()  # Verify credentials work
        set_exchange(exchange)
        return {"status": "configured", "exchange": exchange_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 400
```

## Feature Compatibility

Not all exchanges support all features. Here's how to handle gracefully:

```python
from forven.exchange.ccxt_adapter import CCXTExchange

exchange = CCXTExchange(exchange_id='kraken', api_key='...', api_secret='...')

# Check capabilities
if exchange.ccxt_exchange.has['fetchPositions']:
    positions = await exchange.get_positions()
else:
    print(f"{exchange.exchange_id} doesn't support positions")

if exchange.ccxt_exchange.has['setLeverage']:
    await exchange.set_leverage('BTC/USDT', 2)
else:
    print(f"{exchange.exchange_id} doesn't support leverage")

# These safely return empty/false if not supported
stops = await exchange.place_protective_stop(...)
profits = await exchange.place_take_profit(...)
```

## Testing with CCXT

### Using MockExchange + CCXT Prices

For realistic testing, combine MockExchange with live CCXT price feeds:

```python
from forven.exchange.mock import MockExchange
from forven.exchange.ccxt_adapter import CCXTExchange

# Create a mock exchange for order execution
mock = MockExchange()

# Create CCXT exchange for market data only
ccxt_exchange = CCXTExchange(exchange_id='binance', api_key='', api_secret='')

# Set mock prices from live CCXT
mids = await ccxt_exchange.get_all_mids()
mock.set_mids(mids)

# Now backtest with live prices and mock execution
from forven.exchange.hyperliquid import set_exchange
set_exchange(mock)
```

## Symbol Formatting

Different exchanges use different symbol formats:

| Exchange | Format | Example |
|----------|--------|---------|
| CCXT unified | `base/quote` | `BTC/USDT` |
| Hyperliquid | `base` | `BTC` |
| Binance API | `basequote` | `BTCUSDT` |

The CCXTExchange adapter handles conversion automatically. When you call:

```python
await exchange.market_order('BTC/USDT', 'buy', 1.0)
```

It sends the proper format to CCXT (`BTC/USDT`).

## Market Data Examples

### Get All Prices

```python
mids = await exchange.get_all_mids()
# Returns: {'BTC': 45234.50, 'ETH': 2345.60, ...}
```

### Get Candles

```python
candles = await exchange.get_candles('BTC/USDT', interval='1h', limit=100)
# Returns list of:
# {
#     'timestamp': 1234567890000,
#     'open': 45000.0,
#     'high': 45500.0,
#     'low': 44900.0,
#     'close': 45234.50,
#     'volume': 1234.5,
# }
```

### Get Trade History

```python
fills = await exchange.get_user_fills(symbol='BTC/USDT', limit=50)
# Returns list of:
# {
#     'id': '12345',
#     'symbol': 'BTC/USDT',
#     'side': 'buy',
#     'price': 45000.0,
#     'amount': 0.1,
#     'timestamp': 1234567890000,
# }
```

## Order Execution Examples

### Market Order

```python
result = await exchange.market_order('BTC/USDT', 'buy', 0.1)
if result.success:
    print(f"Bought at market, order ID: {result.order_id}")
else:
    print(f"Error: {result.error}")
```

### Limit Order

```python
result = await exchange.limit_order(
    'BTC/USDT', 'buy', 0.1, price=44000.0
)
if result.success:
    print(f"Limit order placed: {result.order_id}")
```

### Cancel Order

```python
cancelled = await exchange.cancel_order('order123', symbol='BTC/USDT')
if cancelled:
    print("Order cancelled")
```

### Close Position

```python
result = await exchange.close_position('BTC/USDT')
if result.success:
    print(f"Position closed, sold at market")
```

## Error Handling

The CCXT adapter wraps errors gracefully:

```python
from forven.exchange.ccxt_adapter import CCXTExchange

exchange = CCXTExchange(
    exchange_id='binance',
    api_key='invalid_key',
    api_secret='invalid_secret',
)

# This will fail at runtime, not during init
try:
    await exchange.health_check()
except Exception as e:
    print(f"Exchange unavailable: {e}")

# Order execution returns OrderResult with error
result = await exchange.market_order('BTC/USDT', 'buy', 1.0)
if not result.success:
    print(f"Order failed: {result.error}")
```

## Performance Considerations

1. **Rate Limiting**: CCXT respects exchange rate limits by default (`enableRateLimit: True`)
2. **Batch Requests**: Some exchanges support batch operations; CCXT will use them when available
3. **WebSocket vs HTTP**: CCXT uses REST APIs. For low-latency price feeds, consider WebSocket adapters
4. **Caching**: Consider caching symbol metadata and candles locally

## Advanced: Custom CCXT Configuration

Pass any CCXT-specific options via kwargs:

```python
exchange = CCXTExchange(
    exchange_id='binance',
    api_key='...',
    api_secret='...',
    # CCXT options
    enableRateLimit=False,  # Disable rate limiting (risky!)
    enableFetchTradingFees=True,  # Fetch fees
    timeout=30000,  # 30 second timeout
    proxies={'https': 'http://proxy:port'},  # Use proxy
)
```

See [CCXT Documentation](https://docs.ccxt.com) for all options.

## Troubleshooting

### "Exchange not found"
```
ValueError: Exchange 'binace' not found in CCXT
```
**Fix**: Check spelling. Use `binance` not `binace`. See [CCXT Supported Exchanges](https://docs.ccxt.com/manual/docs/exchange-markets).

### "Invalid API Key"
**Fix**: Verify your API key and secret are correct. Check if the exchange requires IP whitelisting.

### "Symbol not found"
```
If 'BTC' symbol is not found
```
**Fix**: Use CCXT format: `'BTC/USDT'` not `'BTC'`. The adapter converts `'BTC'` to `'BTC/USDT'` for the symbol lookup if needed.

### "Feature not supported"
```
Exchange kraken does not support fetchPositions
```
**Fix**: Check `exchange.ccxt_exchange.has['featureName']` before calling. Some exchanges don't support spot positions fetching.

## Integration with Forven

### In Backtesters

```python
from forven.exchange.ccxt_adapter import CCXTExchange
from forven.exchange.hyperliquid import set_exchange

# Configure for testing
exchange = CCXTExchange(
    exchange_id='binance',
    api_key=os.getenv('BINANCE_KEY'),
    api_secret=os.getenv('BINANCE_SECRET'),
    testnet=True,
)
set_exchange(exchange)

# Now all strategy code uses Binance testnet
```

### In Strategy Execution

```python
# In forven/scanner.py or forven/daemon.py
from forven.exchange.sync_wrapper import get_sync_exchange

def execute_strategy():
    exchange = get_sync_exchange()  # Gets current exchange (Hyperliquid, Binance, etc)
    
    # Order execution uses current exchange
    result = exchange.market_order('BTC/USDT', 'buy', 0.1)
    result = exchange.close_position('ETH/USDT')
```

## Migration Roadmap

1. **Phase 1** (Done): Create CCXTExchange adapter and connect to ExchangeInterface ✅
2. **Phase 2**: Add CCXT to Forven's exchange configuration UI
3. **Phase 3**: Support exchange-specific features (margin lending, copy trading, etc)
4. **Phase 4**: Add WebSocket support for low-latency price feeds

## Related Documentation

- [Exchange Interface Guide](./EXCHANGE_INTERFACE.md) - Core abstraction patterns
- [MockExchange Guide](./MOCK_EXCHANGE.md) - Testing without real funds
- [Sync Wrapper Guide](./SYNC_WRAPPER.md) - Using async interface in sync contexts
- [CCXT Official Docs](https://docs.ccxt.com) - Complete CCXT reference
