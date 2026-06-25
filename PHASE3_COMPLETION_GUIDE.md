# Phase 3 Completion Guide: Finishing the High-Risk Migrations

## Progress Summary

**Completed (6 files, ~4,500 LOC migrated):**
- ✅ `axiom/exchange/interface.py` - New interface definition
- ✅ `axiom/exchange/hyperliquid_adapter.py` - HyperliquidExchange implementation
- ✅ `axiom/exchange/mock.py` - MockExchange for testing
- ✅ `axiom/exchange/sync_wrapper.py` - Sync wrapper for sync contexts
- ✅ `axiom/agents/tools_exchange.py` - AI agent execution tools (async)
- ✅ `axiom/soak.py` - Health check (partial)
- ✅ `axiom/api_domains/trading.py` - REST trading API (SyncExchange)
- ✅ `axiom/api_domains/paper_control.py` - Paper trading controls (SyncExchange)

**Remaining (3 critical files, ~11,000 LOC):**
- ⏳ `axiom/exchange/risk.py` - Kill-switches, reconciliation (3,270 LOC)
- ⏳ `axiom/scanner.py` - Core strategy execution (5,963 LOC)
- ⏳ `axiom/daemon.py` - Market data loop (1,994 LOC)

**Remaining (secondary):**
- ⏳ Tests (30 files, 821 LOC) - Use MockExchange instead of mocks
- ⏳ Other agent tools, utilities

---

## High-Risk Files: Detailed Migration Guides

### 1. `axiom/exchange/risk.py` (3,270 LOC)

**Criticality**: 🔴 CRITICAL - Controls kill-switches, emergency closes, liquidation protection

**Current Hyperliquid Imports**:
```python
547:  from axiom.exchange.hyperliquid import close_position, market_order, ...
1005: from axiom.exchange.hyperliquid import get_all_mids, get_open_orders, get_positions
1062: from axiom.exchange.hyperliquid import cancel_order
1130: from axiom.exchange.hyperliquid import get_open_orders
1347: from axiom.exchange.hyperliquid import place_protective_stop
1822: from axiom.exchange.hyperliquid import get_user_fills
2850: from axiom.exchange.hyperliquid import close_position, get_positions
```

**Key Functions to Migrate** (in order of importance):
1. `emergency_flatten_all()` - **CRITICAL**: Kill-switch implementation
   - Uses: `close_position()`, `get_positions()`
   - Impact: Hard stop on all trading
   - Strategy: Use `SyncExchange` wrapper since it's called from sync contexts

2. `reconcile_exchange_positions()` (line 1916)
   - Uses: `get_positions()`, `get_all_mids()`, `get_user_fills()`, `cancel_order()`
   - Impact: Reconciles exchange state with database
   - Strategy: Use `SyncExchange` wrapper, test thoroughly

3. `reconcile_all_books()` (line 2334)
   - Uses: `get_positions()` for each sub-account
   - Impact: Multi-book reconciliation
   - Strategy: Same as above

4. Helper functions (`get_account_value()`, `get_open_orders()` calls)
   - Scattered throughout
   - Strategy: Use SyncExchange wrapper

**Migration Pattern**:
```python
# OLD
from axiom.exchange.hyperliquid import close_position, get_positions

positions = get_positions(testnet=testnet)
close_result = close_position(asset, size, side, testnet=testnet)

# NEW
from axiom.exchange.sync_wrapper import get_sync_exchange

exchange = get_sync_exchange(testnet=testnet)
positions = exchange.get_positions()
close_result = exchange.close_position(asset)
```

**Testing Strategy**:
1. **Unit test**: `pytest tests/test_risk.py -k emergency_flatten -v`
2. **Integration test**: Set daily-loss limit, trigger it, verify kill-switch activates
3. **Reconciliation test**: Run soak check, verify no reconciliation errors
4. **Smoke test**: Paper trade, check that risk controls don't accidentally trigger

**Risk Mitigation**:
- [ ] Create a feature branch for this migration
- [ ] Migrate one function at a time
- [ ] Test each function in isolation before moving to the next
- [ ] Run full test suite after each function
- [ ] Have a rollback plan (revert to previous commit)

---

### 2. `axiom/scanner.py` (5,963 LOC)

**Criticality**: 🔴 CRITICAL - Core strategy execution engine

**Current Hyperliquid Imports**:
```python
436:  from axiom.exchange.hyperliquid import market_order, ...
479:  from axiom.exchange.hyperliquid import set_leverage, ...
572:  from axiom.exchange.hyperliquid import close_position, ...
3075: from axiom.exchange.hyperliquid import get_all_mids, ...
3094: from axiom.exchange.hyperliquid import get_positions, ...
3178: from axiom.exchange.hyperliquid import market_order, ...
3248: from axiom.exchange.hyperliquid import get_account_value, ...
5744: from axiom.exchange.hyperliquid import get_positions, ...
5848: from axiom.exchange.hyperliquid import get_open_orders, ...
```

**Challenges**:
- Mixed async/sync contexts
- Deeply nested functions
- Strategy signal scoring mixed with exchange calls
- Complex state management

**Key Functions to Migrate**:
1. `_execute_opportunity()` - **CRITICAL**: Places orders for signals
   - Uses: `market_order()`, `set_leverage()`, `close_position()`
   - Strategy: This is the hot path - most critical to test
   
2. `_fetch_portfolio_snapshot()` - Read-only market data
   - Uses: `get_all_mids()`, `get_positions()`, `get_account_value()`
   - Strategy: Can be done first (lower risk)

3. `reconcile_positions()` - Reconciliation
   - Uses: `get_positions()`, `get_open_orders()`
   - Strategy: Follow risk.py pattern

**Recommended Approach**:
1. **Start with read-only functions** first (lower risk)
   - `_fetch_portfolio_snapshot()`
   - `_get_live_account_equity()`
   
2. **Then order placement** (core logic)
   - Extract `_execute_opportunity()` into a separate async wrapper
   - Migrate step-by-step within the function

3. **Finally reconciliation** (lower priority)
   - Follow the pattern from risk.py

**Example Refactoring**:
```python
# OLD: sync function calling exchange
def _execute_opportunity(...):
    from axiom.exchange.hyperliquid import market_order
    result = market_order(asset, side, size, ...)

# NEW: async wrapper + sync dispatcher
async def _execute_opportunity_async(exchange, ...):
    result = await exchange.market_order(symbol=asset, side=side, size=size)

def _execute_opportunity(...):
    # Dispatcher to async function
    from axiom.exchange.sync_wrapper import get_sync_exchange
    exchange = get_sync_exchange()
    
    # Call async function via asyncio.run()
    import asyncio
    result = asyncio.run(_execute_opportunity_async(exchange, ...))
```

**Testing Strategy**:
1. **Backtest first**: Run a backtest with MockExchange before touching scanner
   - Set `AXIOM_EXCHANGE=mock` in tests
2. **Paper trading**: Place a small paper trade, verify logs
3. **Live testnet**: If testnet available, verify a single trade
4. **Regression**: Run existing test suite

**Risk Mitigation**:
- [ ] Start with read-only functions
- [ ] Create separate async functions (easier to test)
- [ ] Use asyncio.run() for sync context transitions
- [ ] Test with MockExchange first
- [ ] Deploy to paper trading only (not live)

---

### 3. `axiom/daemon.py` (1,994 LOC)

**Criticality**: 🔴 CRITICAL - Market data loop (price feeds, reconciliation)

**Current Hyperliquid Imports**:
```python
37-44: Direct imports of HyperLiquidFeed, _get_creds, account/position/price fetching
54:    fetch_hyperliquid_candles, dataframe_to_ohlcv_rows
1184:  get_all_mids, get_positions for live price caching and reconciliation
```

**Challenges**:
- Long-running daemon loop
- Async throughout
- WebSocket price feeds
- Reconciliation logic mixed with market data fetching

**Key Components**:
1. `HyperLiquidFeed` class - **WebSocket price feed**
   - Currently: Direct SDK usage
   - Option A: Create custom ExchangeInterface subclass
   - Option B: Keep as-is (not critical to change)

2. `_run_price_loop()` - **Price fetching**
   - Uses: `get_all_mids()`, `get_positions()`
   - Strategy: Already async - can use direct `get_exchange()` calls

3. `reconcile_state()` - Reconciliation
   - Uses: `get_positions()`, `get_account_value()`
   - Strategy: Use interface calls

**Recommended Approach**:
1. **Keep HyperLiquidFeed as-is** for now (complex to refactor)
2. **Migrate the main loop functions**:
   - `_run_price_loop()` → use `get_exchange()` directly (already async)
   - `reconcile_state()` → use interface calls

3. **Example**:
```python
# OLD: daemon.py
from axiom.exchange.hyperliquid import get_all_mids, get_positions

async def _run_price_loop():
    prices = get_all_mids(self._testnet)
    positions = get_positions(self._testnet)

# NEW: daemon.py
from axiom.exchange.hyperliquid import get_exchange

async def _run_price_loop():
    exchange = get_exchange()  # Returns HyperliquidExchange by default
    prices = await exchange.get_all_mids()
    positions = await exchange.get_positions()
```

**Testing Strategy**:
1. **Health check**: `python -m axiom soak` should show daemon status OK
2. **Price feed**: Monitor price updates in logs
3. **Reconciliation**: Run a full cycle, verify no discrepancies

**Risk Mitigation**:
- [ ] Leave WebSocket feed untouched for now
- [ ] Migrate sync data fetches first
- [ ] Keep error handling intact (important for daemon stability)
- [ ] Test with MockExchange in unit tests
- [ ] Monitor logs during live execution

---

## Step-by-Step Completion Roadmap

### Week 1: Foundation & Testing
1. **Day 1-2**: Set up test infrastructure
   - [ ] Create `test_risk_migration.py` - unit tests for risk.py functions
   - [ ] Create `test_scanner_migration.py` - unit tests for scanner.py functions
   - [ ] Update existing tests to use MockExchange instead of SDK mocks

2. **Day 3-4**: risk.py migration
   - [ ] Migrate `emergency_flatten_all()` and supporting functions
   - [ ] Write integration test for emergency stop
   - [ ] Test: `pytest tests/test_risk_migration.py -v`

3. **Day 5**: scanner.py read-only functions
   - [ ] Migrate `_fetch_portfolio_snapshot()` and related functions
   - [ ] Test: Backtest with mock data
   - [ ] Verify logs show correct prices/positions

### Week 2: Core Execution & Daemon
1. **Day 1-3**: scanner.py execution functions
   - [ ] Extract `_execute_opportunity()` logic
   - [ ] Migrate to use interface
   - [ ] Test: Paper trade in UI
   - [ ] Verify order execution logs

2. **Day 4-5**: daemon.py migration
   - [ ] Migrate price loop functions
   - [ ] Migrate reconciliation functions
   - [ ] Test: Health check, price feed updates
   - [ ] Verify daemon logs

### Week 3: Integration & Hardening
1. **Day 1-2**: Full regression testing
   - [ ] Run all tests: `pytest tests/ -v`
   - [ ] Run soak check: `python -m axiom soak`
   - [ ] Manual paper trading: Create, execute, close trade

2. **Day 3-5**: Live testnet (if available)
   - [ ] Small live testnet trades
   - [ ] Monitor for errors
   - [ ] Verify kill-switch works

---

## Success Criteria

- [ ] All 3 high-risk files migrated
- [ ] Full test suite passing: `pytest tests/ -v`
- [ ] Soak check passing: `python -m axiom soak`
- [ ] Paper trading works end-to-end
- [ ] No regression in strategy execution
- [ ] Logs show clean interface usage (no deprecated SDK calls)
- [ ] MockExchange used in all unit tests

---

## Rollback Plan

If anything breaks critically:

```bash
# 1. Identify the broken function
grep -r "from axiom.exchange.hyperliquid import" axiom/

# 2. Revert that file to previous version
git checkout HEAD~ -- axiom/exchange/risk.py

# 3. Restart backend
python -m axiom api

# 4. Diagnose the root cause
# (most likely: async/await mismatch or incorrect interface usage)
```

---

## Next Steps

1. **Immediate** (Today):
   - [ ] Review this guide with the team
   - [ ] Create feature branch: `git checkout -b phase3-high-risk-migration`

2. **Short-term** (This week):
   - [ ] Start with risk.py migration
   - [ ] Set up test infrastructure
   - [ ] Migrate one function at a time

3. **Medium-term** (Next 2 weeks):
   - [ ] Complete scanner.py and daemon.py
   - [ ] Integration testing
   - [ ] Paper trading validation

4. **Long-term** (Future):
   - [ ] Performance optimization (batched orders, websocket consolidation)
   - [ ] Exchange abstraction for other exchanges (CCXT, Binance)
   - [ ] Simulator with realistic fills/slippage

---

## Resources

- Completed examples: `axiom/agents/tools_exchange.py`, `axiom/api_domains/trading.py`
- Sync wrapper: `axiom/exchange/sync_wrapper.py`
- Interface definition: `axiom/exchange/interface.py`
- Migration guide: `EXCHANGE_MIGRATION_GUIDE.md`
- Status: `MIGRATION_STATUS.md`

---

## Questions & Blockers

If you encounter issues:

1. **Async/await mismatch**:
   - Sync context → use SyncExchange wrapper (no await needed)
   - Async context → use get_exchange() directly (use await)
   - Mixing → extract async logic into separate function

2. **Result type mismatch**:
   - SDK returns dict → Interface returns dataclass
   - Solution: `result.raw_response` for raw SDK response
   - Or: Convert to dict manually (see trading.py example)

3. **Exchange method signatures**:
   - Refer to `interface.py` for all available methods
   - Look at completed migrations (tools_exchange.py) for usage examples
   - Use `help(exchange.method_name)` for docstrings

4. **Testing**:
   - Use MockExchange: `from axiom.exchange.mock import MockExchange`
   - Set up: `set_exchange(MockExchange())`
   - Run: `pytest tests/ -v` after changes

---

Generated: 2026-06-24
Status: Ready for Phase 3 completion
