# Exchange Interface Migration Guide

## Summary

This document tracks the migration from tightly-coupled Hyperliquid SDK calls to the new `ExchangeInterface` abstraction. This enables testing with MockExchange, future exchange swaps, and cleaner code architecture.

## Phase 1: Foundation (COMPLETE ✅)

New files created:
- `forven/exchange/interface.py` - Abstract `ExchangeInterface` with async methods
- `forven/exchange/hyperliquid_adapter.py` - `HyperliquidExchange` implementation
- `forven/exchange/mock.py` - `MockExchange` for testing
- `forven/exchange/sync_wrapper.py` - Sync wrappers for sync contexts

Modified files:
- `forven/exchange/hyperliquid.py` - Added `get_exchange()`, `set_exchange()` module functions

Status: Ready for use. All new code can use the interface immediately.

## Phase 2: LLM Config (COMPLETE ✅)

Fixed the `FORVEN_OPERATOR_KEY` initialization issue:
- Added `python -m forven auth init-operator-key` CLI command
- Improved 401 error messages to guide users
- Added startup warnings in `api_core.py`
- Updated `docs/FIRST_RUN_CHECKLIST.md` with proper setup instructions

Status: Users no longer need browser console hacks.

## Phase 3: Code Migration (IN PROGRESS)

### Completed Files

#### 1. `forven/agents/tools_exchange.py` (MIGRATED ✅)

**Pattern Applied:**
- Made tool functions `async` (already expected by the runner)
- Imported `get_exchange()` instead of individual functions
- Used `await exchange.method(...)` for all calls
- Converted `OrderResult` dataclass to dict for JSON serialization

**Functions Updated:**
- `_tool_place_order()` - async, uses `exchange.market_order()` / `exchange.limit_order()`
- `_tool_close_position()` - async, uses `exchange.close_position()`
- `_tool_get_exchange_positions()` - async, uses `exchange.get_positions()`
- `_tool_get_account_info()` - async, uses `exchange.get_account_value()`

**Functions Not Yet Updated:**
- `_tool_cancel_orders()` - Uses `cancel_all_orders()` which isn't in base interface
- `_tool_update_trade()` - Complex logic, can be updated when needed
- `_tool_request_fix()` - Not exchange-related

#### 2. `forven/soak.py` (PARTIALLY MIGRATED ✅)

**Pattern Applied:**
- For sync functions, use `asyncio.run()` to call async interface
- Wrap async calls in helper function

**Function Updated:**
- `_probe_hyperliquid_connection()` - Uses `asyncio.run()` to call async interface

### Remaining Files to Migrate

#### Medium Priority (Update when touching these files)

1. **`forven/api_domains/trading.py`** (1,148 LOC)
   - Pattern: Use `SyncExchange` wrapper or `asyncio.run()` for sync functions
   - Key imports to replace: `market_order`, `limit_order`, `close_position`, `get_open_orders`, `get_positions`
   - Replace with: `from forven.exchange.sync_wrapper import get_sync_exchange; exchange = get_sync_exchange()`

2. **`forven/api_domains/paper_control.py`** (800 LOC)
   - Pattern: Same as trading.py
   - Key imports: `close_position`, `market_order`, `cancel_order`, `place_protective_stop`, `place_take_profit`

#### High Priority (Critical Path - Core Execution)

3. **`forven/scanner.py`** (5,963 LOC - CORE STRATEGY EXECUTION)
   - Most critical file - handles live order execution
   - Challenge: Many nested functions, complex state management
   - Pattern: Make main execution functions async, use `get_exchange()` directly
   - Key functions: `_execute_opportunity()`, `_place_order()`, risk checks
   - **Recommendation**: Migrate incrementally, test thoroughly with MockExchange first

4. **`forven/exchange/risk.py`** (3,270 LOC - CRITICAL RISK CONTROLS)
   - Handles kill-switches, emergency closes, liquidation protection
   - Pattern: Use `SyncExchange` wrapper since many functions are sync paths
   - Key functions: `emergency_flatten_all()`, `reconcile_positions()`, `is_trading_allowed()`
   - **Recommendation**: Migrate function-by-function, test each with MockExchange

5. **`forven/daemon.py`** (1,994 LOC - MARKET DATA LOOP)
   - Handles price feeds and async reconciliation
   - Pattern: Already async in parts, can make async natively
   - Key classes: `HyperLiquidFeed` (consider wrapping as custom Exchange subclass)
   - Key functions: Price fetching, reconciliation loops
   - **Recommendation**: Migrate asyncronous paths first

#### Low Priority (Utility Files)

6. **`forven/agents/tools_backtesting.py`** - Backtesting tools (test-only, not critical)
7. **`forven/agents/tools_brain.py`** - Brain agent tools
8. **`forven/agents/tools_core.py`** - Core tools (mostly file/memory ops)
9. **Tests** (30 files, 821 LOC) - Use `MockExchange` instead of sync mocks

---

## Migration Patterns

### Pattern 1: Async Functions (Preferred)

Use this for functions that are already in async contexts or can be made async.

```python
# OLD
from forven.exchange.hyperliquid import market_order

def my_function():
    result = market_order(symbol, side, size, testnet=True)
    
# NEW
from forven.exchange.hyperliquid import get_exchange

async def my_function():
    exchange = get_exchange()
    result = await exchange.market_order(symbol=symbol, side=side, size=size)
```

### Pattern 2: Sync Functions with asyncio.run()

Use this for sync functions that can't be made async (CLI, callbacks).

```python
# OLD
from forven.exchange.hyperliquid import get_positions

def my_sync_function():
    positions = get_positions(testnet=True)

# NEW
import asyncio
from forven.exchange.hyperliquid import get_exchange

def my_sync_function():
    async def fetch():
        exchange = get_exchange()
        return await exchange.get_positions()
    
    positions = asyncio.run(fetch())
```

### Pattern 3: Sync Wrapper Class

Use this for sync code that makes many exchange calls.

```python
# OLD
from forven.exchange.hyperliquid import get_positions, get_account_value, market_order

def my_risk_function():
    positions = get_positions()
    account = get_account_value()
    market_order(...)

# NEW
from forven.exchange.sync_wrapper import get_sync_exchange

def my_risk_function():
    exchange = get_sync_exchange()
    positions = exchange.get_positions()
    account = exchange.get_account_value()
    result = exchange.market_order(...)
```

### Result Type Conversion

The interface returns `OrderResult` dataclass instead of dicts:

```python
# NEW - Convert to dict when needed for JSON
from dataclasses import asdict
result = await exchange.market_order(...)

result_dict = {
    "success": result.success,
    "order_id": result.order_id,
    "error": result.error,
}
if result.raw_response:
    result_dict.update(result.raw_response)
```

---

## Testing Strategy

### Test with MockExchange

```python
from forven.exchange.mock import MockExchange
from forven.exchange.hyperliquid import set_exchange

# In test setup
mock = MockExchange(initial_balance=10000.0)
await mock.set_mids({"BTC": 50000.0, "ETH": 3000.0})
set_exchange(mock)

# Now your code uses MockExchange instead of real Hyperliquid
```

### Run Before Migration

1. `pytest tests/ -v` - Ensure tests pass with current code
2. Migrate one function/file at a time
3. Run tests after each change
4. Use MockExchange in tests to avoid real orders

---

## Checklist for Completing Phase 3

- [ ] Complete `api_domains/trading.py`
- [ ] Complete `api_domains/paper_control.py`
- [ ] Start `exchange/risk.py` - migrate sync functions first
- [ ] Start `scanner.py` - test incrementally
- [ ] Start `daemon.py` - async paths first
- [ ] Update tests to use MockExchange
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Smoke test: `python -m forven soak` (should report "hyperliquid: ok")
- [ ] Manual test: Execute a small paper trade in the UI
- [ ] Integration test: Backtest → Paper → Review in UI

---

## Notes for High-Risk Files

### `scanner.py` Migration Strategy

This file is the core execution engine. Migrate carefully:

1. **Don't touch the strategy signal scoring logic** - just the order placement
2. **Identify the call stack**: `Brain` → `scanner` → `_execute_opportunity` → exchange calls
3. **Make exchange calls async**:
   ```python
   # OLD: inside sync _execute_opportunity
   result = market_order(symbol, side, size)
   
   # NEW: make the function async all the way up
   async def _execute_opportunity(...):
       exchange = get_exchange()
       result = await exchange.market_order(...)
   ```
4. **Test each piece**: After updating each function, backtest and check logs

### `daemon.py` Migration Strategy

The daemon is already async. Strategy:

1. **Update market data fetches** first:
   ```python
   # OLD
   prices = get_all_mids(testnet=self.testnet)
   
   # NEW
   exchange = get_exchange()
   prices = await exchange.get_all_mids()
   ```
2. **Leave price websocket as-is** if it works
3. **Test price feed**: Run daemon and check `http://localhost:8003/api/health`

### `risk.py` Migration Strategy

This file has complex state. Approach:

1. **Start with read-only calls**: `get_positions()`, `get_account_value()`, `get_all_mids()`
2. **Then write operations**: `close_position()`, `cancel_order()`, `place_protective_stop()`
3. **Test each function** in isolation first
4. **Integration test**: Set daily loss limit, trigger it, watch kill-switch

---

## Success Criteria

The migration is complete when:

1. ✅ All files import from `forven.exchange.hyperliquid` using `get_exchange()` or `get_sync_exchange()`
2. ✅ No direct imports of SDK functions like `from hyperliquid.exchange import Exchange`
3. ✅ Tests use `MockExchange` instead of mocking hyperliquid.py
4. ✅ Full test suite passes: `pytest tests/ -v`
5. ✅ Live UI demo: Place a paper trade, close it, verify no errors
6. ✅ Soak check passes: `python -m forven soak`

---

## Rollback Plan

If migration breaks something critical:

1. The old sync functions in `hyperliquid.py` still work (backwards compatible)
2. For a quick rollback, revert to the previous commit: `git revert <commit>`
3. The interface is opt-in - new code uses it, old code continues to work

---

## Related Issues / Next Steps

- After migration: Update tests to use MockExchange (see `mock.py` for examples)
- Consider: Wrap `HyperLiquidFeed` as an ExchangeInterface subclass for the daemon
- Consider: Add `get_funding_rates()`, `get_leverage_limits()` to interface for future use
- Consider: Add batch order operations to interface for efficiency

