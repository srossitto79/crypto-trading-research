# Exchange Choice Guide: When to Use Which

## Quick Decision Tree

```
Do you need to test/paper-trade without risking capital?
    ├─ YES → Use MockExchange
    │        (In-memory, instant fills, safe)
    │
    └─ NO → Do you have a Hyperliquid account?
            ├─ YES → Use HyperliquidExchange (default)
            │        (Works as before, no changes needed)
            │
            └─ NO → Use CCXTExchange with your preferred exchange
                   ├─ Most liquid: binance
                   ├─ US-friendly: kraken or coinbase
                   ├─ Advanced features: okx or bybit
                   └─ All others: see CCXT docs
```

---

## Exchange Comparison

| Use Case | Exchange | Why | Code |
|----------|----------|-----|------|
| **Safe testing** | MockExchange | In-memory, no capital risk | `MockExchange()` |
| **Paper trading** | MockExchange + CCXT mids | Live prices, safe execution | `set_mids(await ccxt.get_all_mids())` |
| **Live Hyperliquid** | HyperliquidExchange | Default, works as-is | `get_exchange()` |
| **Live Binance** | CCXTExchange | Most liquid, best UI | `CCXTExchange(exchange_id='binance', ...)` |
| **Live Kraken** | CCXTExchange | Regulated, US-friendly | `CCXTExchange(exchange_id='kraken', ...)` |
| **Live Coinbase** | CCXTExchange | Simplest API, regulated | `CCXTExchange(exchange_id='coinbase', ...)` |
| **Backtesting** | Multiple at once | Compare across exchanges | Loop over `exchange_ids` |
| **CI/CD tests** | MockExchange | No external dependencies | `MockExchange()` |

---

## Scenario-Based Recommendations

### Scenario 1: "I want to test my strategy without risking money"

**Solution**: MockExchange

```python
from forven.exchange import MockExchange
from forven.exchange.hyperliquid import set_exchange

# Safe: No capital, instant fills, no network calls
mock = MockExchange()
set_exchange(mock)

# Now run backtest, paper trade, whatever you want
```

**Pros**: ✅ Safe, ✅ Fast, ✅ No capital needed, ✅ No API keys needed
**Cons**: ❌ Prices are configurable (not always live), ❌ Fills are instant (not realistic)

### Scenario 2: "I'm using Hyperliquid already"

**Solution**: Keep using HyperliquidExchange (no changes needed)

```python
# Just works—no code changes required
from forven.exchange import get_exchange

exchange = get_exchange()
await exchange.market_order('BTC', 'buy', 0.1)
```

**Pros**: ✅ No changes needed, ✅ Works now, ✅ Most tested
**Cons**: ❌ Requires deposit, ❌ Testnet still needs deposit

### Scenario 3: "I want to trade on Binance"

**Solution**: CCXTExchange with Binance

```python
from forven.exchange import CCXTExchange
from forven.exchange.hyperliquid import set_exchange

exchange = CCXTExchange(
    exchange_id='binance',
    api_key='YOUR_KEY',
    api_secret='YOUR_SECRET',
    testnet=True,  # Use testnet first
)
set_exchange(exchange)

# Now all code uses Binance
```

**Pros**: ✅ Most liquid, ✅ Good testnet, ✅ Familiar UI, ✅ Competitive fees
**Cons**: ❌ China-based, ❌ API can change

### Scenario 4: "I want to try multiple exchanges"

**Solution**: Create instances and swap at runtime

```python
from forven.exchange import CCXTExchange
from forven.exchange.hyperliquid import set_exchange, get_exchange

exchanges = {
    'binance': CCXTExchange(exchange_id='binance', api_key='...', api_secret='...'),
    'kraken': CCXTExchange(exchange_id='kraken', api_key='...', api_secret='...'),
    'okx': CCXTExchange(exchange_id='okx', api_key='...', api_secret='...'),
}

for name, exchange in exchanges.items():
    set_exchange(exchange)
    current = get_exchange()
    balance = await current.get_account_value()
    print(f"{name}: ${balance}")
```

**Pros**: ✅ Easy comparison, ✅ All code works unchanged
**Cons**: ❌ Need multiple API keys, ❌ More setup

### Scenario 5: "I want paper trading with REAL prices"

**Solution**: MockExchange + CCXT price sync

```python
from forven.exchange import CCXTExchange, MockExchange
from forven.exchange.hyperliquid import set_exchange

# Get live prices from Binance (no auth needed for public data)
binance = CCXTExchange(exchange_id='binance', api_key='', api_secret='')
mids = await binance.get_all_mids()

# Trade safely with MockExchange
mock = MockExchange()
mock.set_mids(mids)
set_exchange(mock)

# Now paper trade with live Binance prices
```

**Pros**: ✅ Safe, ✅ Live prices, ✅ No capital needed, ✅ No auth needed
**Cons**: ❌ Prices refresh once per call (not streaming), ❌ Fills are instant

### Scenario 6: "I'm building a strategy that might move between exchanges"

**Solution**: Use the abstraction from day one

```python
# Don't hardcode exchange—use the interface
from forven.exchange.hyperliquid import get_exchange

async def execute_trade():
    exchange = get_exchange()  # Gets whatever is configured
    await exchange.market_order('BTC/USDT', 'buy', 1.0)

# User can swap exchange anytime
# Code never changes
```

**Pros**: ✅ Future-proof, ✅ Testable, ✅ Swappable
**Cons**: ❌ Slightly more abstraction

---

## Feature Comparison Table

| Feature | MockExchange | Hyperliquid | Binance (CCXT) | Kraken (CCXT) |
|---------|---|---|---|---|
| **Setup** | Zero config | Deposit required | API keys | API keys |
| **Capital required** | ❌ None | ✅ Yes | ✅ Yes | ✅ Yes |
| **Testnet available** | ✅ All modes | ❌ No | ✅ Yes | ✅ Yes |
| **Spot trading** | ✅ | ✅ | ✅ | ✅ |
| **Margin trading** | ✅ (configurable) | ✅ | ✅ | ✅ |
| **Futures** | ✅ (simulated) | ✅ | ✅ | ✅ |
| **Leverage** | ✅ (1-100x) | ✅ | ✅ | ✅ |
| **Stop-loss** | ✅ | ✅ | ✅ | ✅ |
| **Take-profit** | ✅ | ✅ | ✅ | ✅ |
| **Fill speed** | Instant | 1-10s | 1-10s | 1-10s |
| **Price feeds** | Configurable | Live | Live | Live |
| **Order latency** | None | 10-100ms | 100-500ms | 100-500ms |
| **Fees** | None | Variable | 0.1% | 0.26% |
| **Liquidity** | Unlimited | High | Very high | High |
| **API limits** | Unlimited | Variable | 1200/min | 15/sec |
| **Stability** | ✅ Always | ✅ Yes | ✅ Yes | ✅ Yes |
| **Support** | Built-in | Official | Official | Official |

---

## Step-by-Step: Starting from Scratch

### Step 1: Test with MockExchange (Safe)
```python
from forven.exchange import MockExchange
mock = MockExchange()
set_exchange(mock)
# Run your strategy with zero risk
```

### Step 2: Paper trade with live prices
```python
from forven.exchange import CCXTExchange, MockExchange
binance = CCXTExchange(exchange_id='binance', api_key='', api_secret='')
mock = MockExchange()
mock.set_mids(await binance.get_all_mids())
set_exchange(mock)
# Now test with real Binance prices
```

### Step 3: Go live on testnet
```python
exchange = CCXTExchange(
    exchange_id='binance',
    api_key='testnet_key',
    api_secret='testnet_secret',
    testnet=True
)
set_exchange(exchange)
# Small live trades on testnet
```

### Step 4: Go live on real capital
```python
exchange = CCXTExchange(
    exchange_id='binance',
    api_key='real_key',
    api_secret='real_secret',
    testnet=False  # or omit, defaults to false
)
set_exchange(exchange)
# Full live trading
```

---

## Common Pitfalls & Solutions

### Pitfall 1: "I deployed to testnet but it still used my real balance"
**Problem**: Forgot `testnet=True`
```python
# ❌ Wrong: Uses real account
exchange = CCXTExchange(exchange_id='binance', api_key='...', api_secret='...')

# ✅ Right: Uses testnet
exchange = CCXTExchange(
    exchange_id='binance',
    api_key='...',
    api_secret='...',
    testnet=True
)
```

### Pitfall 2: "My test is hitting the real exchange"
**Problem**: Forgot to set MockExchange
```python
# ❌ Wrong: Uses live API
async def test_strategy():
    exchange = CCXTExchange(...)

# ✅ Right: Uses safe mock
async def test_strategy():
    set_exchange(MockExchange())
    exchange = get_exchange()
```

### Pitfall 3: "I get 'Exchange not found' error"
**Problem**: Typo in exchange ID
```python
# ❌ Wrong
CCXTExchange(exchange_id='binannce')  # Typo!

# ✅ Right
CCXTExchange(exchange_id='binance')   # Correct
```

### Pitfall 4: "My prices don't update in MockExchange"
**Problem**: Set once, never refresh
```python
# ❌ Wrong: Stale prices
mock = MockExchange()
mock.set_mids({'BTC': 45000})
# ... later, prices change but mock doesn't know

# ✅ Right: Refresh regularly
while True:
    mids = await binance.get_all_mids()
    mock.set_mids(mids)
    await asyncio.sleep(60)  # Update every minute
```

---

## Decision: Which One to Choose?

**Choose MockExchange if**:
- You're testing strategies (safe, no capital)
- You're doing CI/CD testing (no network needed)
- You're learning Forven (zero risk)
- You want instant feedback (no network latency)

**Choose HyperliquidExchange if**:
- You already have Hyperliquid account (works now)
- You want perpetuals with high leverage (best features)
- You've tested with Hyperliquid before (known API)

**Choose CCXTExchange (Binance) if**:
- You want the most liquid exchange
- You want testnet availability
- You want lowest fees
- You want familiar interface
- You're in any country (widely available)

**Choose CCXTExchange (Kraken) if**:
- You're in the US or EU
- You value regulatory compliance
- You like European exchanges
- You prefer simpler API

**Choose CCXTExchange (OKX) if**:
- You want advanced features
- You want high leverage futures
- You're in Asia
- You like Chinese exchanges

**Choose CCXTExchange (Coinbase) if**:
- You want simplicity
- You value US regulation
- You only do spot trading
- You have Coinbase account already

---

## Environment Variable Cheat Sheet

```bash
# .env file

# For MockExchange (safe testing)
EXCHANGE_ID=mock

# For Hyperliquid (default, requires deposit)
EXCHANGE_ID=hyperliquid

# For Binance (most popular)
EXCHANGE_ID=binance
EXCHANGE_API_KEY=your_binance_key
EXCHANGE_API_SECRET=your_binance_secret
EXCHANGE_USE_TESTNET=true

# For Kraken (US-friendly)
EXCHANGE_ID=kraken
EXCHANGE_API_KEY=your_kraken_key
EXCHANGE_API_SECRET=your_kraken_secret

# For OKX (advanced)
EXCHANGE_ID=okx
EXCHANGE_API_KEY=your_okx_key
EXCHANGE_API_SECRET=your_okx_secret
EXCHANGE_PASSPHRASE=your_okx_passphrase
```

---

## Summary

| Goal | Exchange | Setup Time | Risk |
|------|----------|-----------|------|
| Learn & test | MockExchange | 1 minute | None |
| Paper trade | MockExchange + CCXT | 5 minutes | None |
| Test on testnet | CCXT testnet | 10 minutes | Low |
| Live trading | CCXT or Hyperliquid | 30 minutes | High |

**Start with MockExchange. Move to testnet. Go live only when confident.**

---

## Links

- [Full CCXT Integration Guide](CCXT_INTEGRATION.md)
- [MockExchange Documentation](MOCK_EXCHANGE.md)
- [Exchange Interface Guide](EXCHANGE_INTERFACE.md)
- [CCXT Official Docs](https://docs.ccxt.com)
