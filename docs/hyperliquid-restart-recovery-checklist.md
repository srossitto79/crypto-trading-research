# Hyperliquid Restart Recovery Checklist

Last updated: 2026-03-12

## Purpose

Use this checklist to harden Hyperliquid restart recovery so a daemon/backend restart cannot leave live exchange exposure unmanaged, invisible, or unprotected.

## How to use this file

- Keep completed items checked.
- Add a dated note whenever we land a fix, change runtime behavior, or validate a recovery scenario.
- Do not re-enable new entries until all `P0` acceptance items are checked.

## Incident Anchor

- Hyperliquid testnet held an open BTC long while Axiom showed `0` open positions.
- The live residual size matched trade `E0095` (`0.00471 BTC`).
- The live Hyperliquid reduce-only stop order `49994803084` matched `E0095.signal_data.exchange_stop_order_id`.
- The daemon reconciler detected `missing_in_sqlite` but did not adopt the exchange position back into active management.
- A restart in the middle of this lifecycle is considered a critical safety scenario.

## Locked Decisions

- [x] Operator pause and recovery block stay independent.
- [x] Recovery completion must not auto-clear an operator pause.
- [x] Auto-match order is strict: exact order ID first, then exact normalized asset/direction/size, then time as a tie-breaker only.
- [x] If multiple candidates remain, recovery must stop and require operator review.
- [x] Recovered positions get a new OPEN trade row with `source='exchange_recovered'`; we do not silently mutate historical closed trades.
- [x] Recovery adoption and risk registration must be atomic.
- [x] Every recovery pass gets a `recovery_batch_id` for rollback.
- [x] Unprotected recovered positions keep trading blocked until protection is attached or an operator resolves the incident.

## P0 - Prevent Restart Exposure Drift

### Gate Consistency

- [x] Standardize the trading gate on `system_state.paused` and keep backward-compatible reads for legacy pause keys.
- [x] Make `daemon_state.recovery_active` an independent hard block inside `is_trading_allowed()`.
- [x] Confirm `operator pause + recovery active` blocks trading.
- [x] Confirm `recovery resolved + operator pause still active` remains blocked.
- [x] Expose both states clearly in dashboard/control-plane responses.

### Startup Preflight

- [x] Add a startup recovery preflight in `axiom.daemon.run()` before entering the main loop.
- [x] Fetch Hyperliquid positions before scanner execution resumes.
- [x] Fetch Hyperliquid open orders before scanner execution resumes.
- [x] Fetch Hyperliquid account snapshot before scanner execution resumes.
- [x] Persist recovery state in `daemon_state`:
- [x] `recovery_active`
- [x] `recovery_status`
- [x] `recovery_started_at`
- [x] `recovery_position_count`
- [x] `recovery_discrepancy_count`
- [x] `recovery_requires_operator`
- [x] `recovery_batch_id`
- [x] Block new entries until startup recovery resolves cleanly.
- [x] Confirm resident daemon/backend are running the intended on-disk code before validating behavior.

### Exchange Snapshot And Matching

- [x] Split reconciliation into explicit helpers for snapshot, match, adopt, and protection validation.
- [x] Match by exact `entry_exchange_order_id` when present.
- [x] Match by exact `exchange_stop_order_id` when present.
- [x] Match by exact normalized `asset + direction + size` only when the candidate is unique.
- [x] Use time proximity only to break ties between already-valid candidates.
- [x] Refuse automatic adoption when matching remains ambiguous.
- [x] Record the match reason in recovery metadata.

### Atomic Adoption

- [x] Create a single helper that writes the recovered OPEN trade row and `portfolio_positions` row in one transaction.
- [x] Stamp recovered rows with `source='exchange_recovered'`.
- [x] Stamp recovered rows with `signal_data.recovery_batch_id`.
- [x] Stamp recovered rows with `signal_data.recovery_reason`.
- [x] Stamp recovered rows with `signal_data.recovered_from_trade_id` when a prior trade was matched.
- [x] Rebuild risk state from open trades after recovery adoption completes.
- [x] Confirm no read path can observe a recovered trade without a matching `portfolio_positions` row.

### Protection Validation

- [x] Reuse an existing live reduce-only stop order if one is already on exchange.
- [x] If no live stop exists, reuse the matched trade's prior stop from `signal_data` when it is still sane.
- [x] Define and implement a deterministic emergency stop policy for recovered positions.
- [x] Clamp emergency stop placement with a dedicated max-distance safety setting.
- [x] If no sane stop can be derived, keep recovery blocked and require operator action.
- [x] Record stop provenance in recovery metadata.

### Recovery Visibility

- [x] Force exchange verification in `read_open_trades()` whenever recovery is active.
- [x] Force exchange verification in `read_open_trades()` whenever reconciliation issues are present.
- [x] Surface recovered positions in dashboard open-positions views.
- [x] Surface recovered positions in the trades workspace.
- [x] Add UI labels for:
- [x] `Recovered`
- [x] `Recovery blocking entries`
- [x] `Needs protection`
- [x] `Exchange-backed`
- [x] Add operator-visible recovery summaries to `ops_manual_action_state`.

### Rollback

- [x] Add a rollback helper keyed by `recovery_batch_id`.
- [x] Rollback must pause entries before reverting recovery rows.
- [x] Rollback must clean up recovered OPEN rows from the target batch.
- [x] Rollback must rebuild `portfolio_positions` from the remaining OPEN trades.
- [x] Rollback must clear stale recovery markers from daemon state.

### P0 Tests

- [x] Daemon startup with exchange position and empty SQLite adopts the position before entries resume.
- [x] Daemon startup with exchange position and missing `portfolio_positions` restores risk registration.
- [x] Daemon startup with exchange position and existing live stop binds protection correctly.
- [x] Daemon startup with exchange position and no stop remains blocked and marks `recovery_requires_operator=true`.
- [x] Daemon startup with ambiguous match candidates requires operator review.
- [x] Inverse orphan case: SQLite shows OPEN while exchange is flat.
- [x] Combined gate case: operator pause plus recovery active.
- [x] Recovery rollback by `recovery_batch_id`.

### P0 Acceptance Criteria

- [x] After restart, any live Hyperliquid position is visible in Axiom within seconds.
- [x] After restart, any recovered position is back under risk management before new entries are allowed.
- [x] Restart cannot leave exchange exposure invisible while the UI shows `0` open positions.
- [x] Restart cannot resume new entries while a recovered position lacks protection.
- [x] Recovery status is visible in both backend status payloads and the UI.

## P1 - Close Path Truthfulness

- [x] Add `pending_close_reconcile` handling for close requests.
- [x] Stop treating requested close prices as confirmed exit fills.
- [x] Stop finalizing local closes until the exchange is flat or a real exit fill is confirmed.
- [x] Preserve recovery/adoption logic across restarts when a close is still pending reconciliation.
- [x] Cancel or retire linked stop/take-profit orders on confirmed close.
- [x] Add regression tests for restart during pending close reconciliation.
- [x] Add regression tests for exchange-flat but SQLite-open cleanup.

## P2 - Reporting And Operator Clarity

- [x] Persist a full exchange-backed account snapshot, not just `account_equity`.
- [x] Separate `Account Equity`, `Available To Trade`, and `Margin Used` in the dashboard.
- [x] Unify network resolution so diagnostics always state the actual network used.
- [x] Surface reconciliation issues and recovery summaries in the main dashboard and ops views.
- [x] Confirm exchange-backed paper positions remain visible by default.

## P3 - Re-entry And Scanner Hardening

- [x] Preserve same-bar re-entry lock state when entry fingerprints are updated.
- [x] Verify a strategy cannot reopen on the same bar after a recovered or recently closed position.
- [x] Consider an asset-level cooldown so multiple paper strategies cannot serially reopen the same asset on one bar without explicit policy.

## Notes

- `2026-03-12 - Checklist created to drive Hyperliquid restart recovery hardening.`
- `2026-03-12 - Landed P0 backend slice for recovery-aware trading gate, daemon startup preflight, and recovery status exposure. Targeted tests passed for recovery gate, dashboard recovery payload, and daemon startup preflight helpers.`
- `2026-03-12 - Added strict exchange adoption on startup: stop-order and exact size matching, ambiguous-match blocking, atomic recovered trade + portfolio registration, and restart tests for adoption/risk restoration.`
- `2026-03-12 - Added protection-aware recovery blocking and automatic exchange verification for open-trade views during recovery or reconciliation issues.`
- `2026-03-12 - Surfaced recovery state in the dashboard/trades UI with recovery banners plus Recovered, Exchange-backed, and Needs protection badges.`
- `2026-03-12 - Fixed inverse-orphan startup handling so SQLite OPEN trades are auto-closed and treated as resolved when Hyperliquid is already flat.`
- `2026-03-12 - Added ops-facing recovery summaries in ops_manual_action_state, made manual exchange reconcile recovery-aware, and added rollback by recovery_batch_id with forced pause + portfolio rebuild.`
- `2026-03-12 - Added restart-time protection repair: reuse sane prior stops, place deterministic emergency stops when needed, and keep recovery blocked if stop restoration fails.`
- `2026-03-12 - Landed pending-close reconciliation so close requests stay OPEN until exchange-flat confirmation, and confirmed close cleanup now retires linked reduce-only protection orders.`
- `2026-03-12 - Added strict recovery matching by entry_exchange_order_id, explicit exchange snapshot helpers, and a read-path safeguard that rebuilds portfolio risk rows before recovered trades are surfaced.`
- `2026-03-12 - Cached full Hyperliquid account snapshots in daemon state, unified network resolution across daemon/scanner/trading/soak paths, and surfaced equity vs available vs margin in the dashboard.`
- `2026-03-12 - Added runtime code fingerprints to backend status plus scanner same-bar hardening that preserves strategy locks and blocks serial asset re-entry on the same candle, including restart-aware DB fallback checks.`
