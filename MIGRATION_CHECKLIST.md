# Forven Exchange Abstraction Migration - Checklist

## Overview
This checklist tracks the migration from hard-coded Hyperliquid SDK to the new pluggable `ExchangeInterface` abstraction.

**Target**: Make all exchange calls go through the interface, enabling testing with MockExchange and future exchange swaps.

---

## Phase 1: Foundation ✅ COMPLETE

### Core Abstraction
- [x] Create `forven/exchange/interface.py` (abstract interface)
- [x] Create `forven/exchange/hyperliquid_adapter.py` (HyperliquidExchange impl)
- [x] Create `forven/exchange/mock.py` (MockExchange for testing)
- [x] Create `forven/exchange/sync_wrapper.py` (sync wrapper)
- [x] Update `forven/exchange/hyperliquid.py` (module-level get_exchange/set_exchange)

### Verification
- [x] All new code compiles
- [x] Backwards compatible (old imports still work)
- [x] MockExchange works for testing

---

## Phase 2: Config Fix ✅ COMPLETE

### LLM Configuration
- [x] Add `python -m forven auth init-operator-key` CLI command
- [x] Improve 401 error messages in `api_security.py`
- [x] Add startup warning in `api_core.py`
- [x] Update `docs/FIRST_RUN_CHECKLIST.md`

### Verification
- [x] No more browser console `set-storage` hacks needed
- [x] Users get clear guidance on setting up operator key
- [x] Error messages are actionable

---

## Phase 3: Code Migration (IN PROGRESS)

### Low-Risk Files ✅ COMPLETE

#### Tools & Health Checks
- [x] `forven/agents/tools_exchange.py` (4/7 functions)
  - [x] `_tool_place_order()` → async with interface
  - [x] `_tool_close_position()` → async with interface
  - [x] `_tool_get_exchange_positions()` → async with interface
  - [x] `_tool_get_account_info()` → async with interface
  - [ ] `_tool_cancel_orders()` (uses cancel_all_orders, skipped for now)
  - [ ] `_tool_update_trade()` (complex, can be done later)
  - [ ] `_tool_request_fix()` (not exchange-related)

- [x] `forven/soak.py` (partial)
  - [x] `_probe_hyperliquid_connection()` → asyncio.run() wrapper
  - [ ] More health checks can be migrated

### Medium-Risk Files ✅ COMPLETE

#### REST API Domain
- [x] `forven/api_domains/trading.py` (1,148 LOC)
  - [x] `_resolve_exchange_testnet()` → removed SDK dependency
  - [x] `_extract_exchange_open_positions()` → SyncExchange
  - [x] `_cancel_reduce_only_orders_for_asset()` → SyncExchange
  - [x] Position close functions (2) → SyncExchange

- [x] `forven/api_domains/paper_control.py` (800 LOC)
  - [x] `_close_live_trade()` → SyncExchange
  - [x] `_live_reduce()` → SyncExchange
  - [x] `open_manual_position()` → SyncExchange
  - [x] `_cancel_live_order()` → SyncExchange
  - [x] `_place_live_protective()` → SyncExchange

### High-Risk Files ⏳ IN PROGRESS

#### Critical Path
- [ ] `forven/exchange/risk.py` (3,270 LOC) - See PHASE3_COMPLETION_GUIDE.md
  - [ ] `emergency_flatten_all()` - Kill-switch
  - [ ] `reconcile_exchange_positions()` - Reconciliation
  - [ ] `reconcile_all_books()` - Multi-book reconciliation
  - [ ] Utility functions

- [ ] `forven/scanner.py` (5,963 LOC) - See PHASE3_COMPLETION_GUIDE.md
  - [ ] `_fetch_portfolio_snapshot()` - Market data
  - [ ] `_execute_opportunity()` - Order execution
  - [ ] Reconciliation functions
  - [ ] Risk checks

- [ ] `forven/daemon.py` (1,994 LOC) - See PHASE3_COMPLETION_GUIDE.md
  - [ ] `_run_price_loop()` - Price fetching
  - [ ] `reconcile_state()` - Reconciliation
  - [ ] Keep HyperLiquidFeed as-is (for now)

### Tests ⏳ TO DO
- [ ] Update 30 test files to use MockExchange (821 LOC)
- [ ] Remove direct hyperliquid SDK mocks
- [ ] Add integration tests for each migrated function

---

## Verification Checklist

### After Phase 1
- [x] New code compiles
- [x] Imports work: `from forven.exchange.interface import ExchangeInterface`
- [x] Backwards compat: `from forven.exchange import hyperliquid; hyperliquid.market_order(...)`

### After Phase 2
- [x] CLI command works: `python -m forven auth init-operator-key`
- [x] Error messages improved
- [x] Documentation updated

### After Phase 3 (Daily Checklist)
- [ ] `pytest tests/ -v` - Full test suite passes
- [ ] `python -m forven soak` - Health check passes
- [ ] Manual test: Paper trade works end-to-end
- [ ] MockExchange used in all tests (no real trades)
- [ ] No exceptions with "hyperliquid" SDK in logs

### Final Verification
- [ ] All 14+ files migrated
- [ ] Test coverage > 80%
- [ ] Zero SDK calls outside of hyperliquid_adapter.py
- [ ] MockExchange usable for all strategy testing
- [ ] Can swap exchanges without code changes

---

## Metrics

| Metric | Target | Status |
|--------|--------|--------|
| LOC migrated | 27,000+ | 4,500 ✅ |
| Files migrated | 14+ | 8 ✅ |
| Test coverage | >80% | ? |
| Backwards compatible | 100% | ✅ |
| Exchange-agnostic | Yes | Partial ✅ |

---

## Files for Reference

- `MIGRATION_STATUS.md` - Overall status and completed work
- `EXCHANGE_MIGRATION_GUIDE.md` - Patterns and how-to guide
- `PHASE3_COMPLETION_GUIDE.md` - Detailed roadmap for remaining work
- Completed example: `forven/agents/tools_exchange.py`
- Completed example: `forven/api_domains/trading.py`
- Sync wrapper: `forven/exchange/sync_wrapper.py`

---

## Quick Links

### View Migrations
```bash
# See all imports of hyperliquid SDK
grep -r "from forven.exchange.hyperliquid import" forven/ | grep -v "__pycache__"

# See which files still use old patterns
grep -r "from forven.exchange import hyperliquid as" forven/
```

### Testing
```bash
# Run full test suite
pytest tests/ -v

# Run specific file tests
pytest tests/test_tools_exchange.py -v

# Run soak check
python -m forven soak
```

### Manual Testing
```bash
# Start backend
python -m forven api

# In UI: /lab → Create strategy → Backtest → Paper trade
# Expected: No "hyperliquid" SDK errors in logs
```

---

## Next Steps

1. **Review** this checklist with the team
2. **Start Phase 3** - see PHASE3_COMPLETION_GUIDE.md
3. **Migrate risk.py** first (most critical)
4. **Test thoroughly** before moving to scanner.py
5. **Update tests** to use MockExchange
6. **Monitor logs** for SDK usage

---

Last Updated: 2026-06-24  
Status: 6 files complete, 3 critical files remaining
