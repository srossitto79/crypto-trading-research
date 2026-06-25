# LESSONS.md — Trading Intelligence Insights

Pure insight capture. No tables, no backtest dumps. Just "learned X because Y."
Updated as experiments complete. Read this before designing any new strategy.

---

## Market & Regime

(Lessons about market conditions and regime detection go here)

---

## Strategy Design

(Lessons about strategy construction, parameters, and signal types go here)

---

## Infrastructure

(Lessons about tools, APIs, and system behavior go here)

---

## Risk & Sizing

(Lessons about position sizing, Kelly criterion, and risk management go here)

---

## Process

(Lessons about research methodology and workflow go here)

---

*Last updated: (date)*
# S50209 Review Summary (March 27, 2026)

## Investigation Results

### Finding 1: Strategy File EXISTS
The strategy type `funding_mean_reversion_v2` exists at:
`axiom/strategies/custom/funding_mean_reversion_v2.py`

This is NOT the same issue as S50147 (strategy type doesn't exist).

### Finding 2: S50209 Does NOT Exist in Database
Searched all tables (strategies, backtest_results, strategy_candidates, tasks, agent_tasks) - S50209 is NOT in the database.

### Finding 3: Backtest Failure Reason
Running backtest on `funding_mean_reversion_v2` produces `'timestamp'` error, suggesting:
- Strategy may not be properly registered in the strategy registry
- Or data enrichment (funding_rate) is missing required timestamp column

### Conclusion
- S50209 container appears to be phantom/not created
- The actual strategy type EXISTS but cannot be backtested via `run_backtest`
- This is a **registration/registry issue**, not a missing code issue

## Action Items
1. If S50209 was supposed to exist, re-create it properly
2. Investigate why `funding_mean_reversion_v2` fails with timestamp error
3. Check if custom strategies require explicit registration before `run_backtest` can use them# S50209 Review Summary (March 27, 2026)

## Investigation Results

### Finding 1: Strategy File EXISTS
The strategy type `funding_mean_reversion_v2` exists at:
`axiom/strategies/custom/funding_mean_reversion_v2.py`

This is NOT the same issue as S50147 (strategy type doesn't exist).

### Finding 2: S50209 Does NOT Exist in Database
Searched all tables (strategies, backtest_results, strategy_candidates, tasks, agent_tasks) - S50209 is NOT in the database.

### Finding 3: Backtest Failure Reason
Running backtest on `funding_mean_reversion_v2` produces `'timestamp'` error, suggesting:
- Strategy may not be properly registered in the strategy registry
- Or data enrichment (funding_rate) is missing required timestamp column

### Conclusion
- S50209 container appears to be phantom/not created
- The actual strategy type EXISTS but cannot be backtested via `run_backtest`
- This is a **registration/registry issue**, not a missing code issue

## Action Items
1. If S50209 was supposed to exist, re-create it properly
2. Investigate why `funding_mean_reversion_v2` fails with timestamp error
3. Check if custom strategies require explicit registration before `run_backtest` can use them