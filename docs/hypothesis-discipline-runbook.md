# Hypothesis Discipline Runbook

How to operate the hypothesis refinement loop after the 2026-04-17 redesign.
This document is the answer to "why hasn't a new hypothesis appeared today?"
and similar operator questions.

---

## What changed

Before: the system created hundreds of shallow hypotheses (one strategy per
hypothesis, then move on). Each hypothesis was treated as a one-off; verdicts
were essentially never written.

After: the system works each hypothesis until it is **proven** or **disproven**,
under three discipline mechanics:

1. **Active-pool pressure valve** — a target population of N hypotheses sits at
   `manager_state='active'`. When a create arrives and the pool is already at
   cap, the system auto-archives the weakest active hypothesis (fewest linked
   strategies, then stalest `updated_at`, then oldest `created_at`) to make
   room. Research agents and operators are never refused. `HypothesisPoolFullError`
   is now a defensive fallback that fires only if no eviction victim can be
   found — structurally unusual.
2. **Round-robin depth** — the promotion loop will not re-pick the same
   hypothesis until at least `min_per_pick` strategies have been created since
   its last dispatch. Forces breadth.
3. **Verdict math floor** — the LLM verdict is upper-bounded by hard math:
   `proven` requires hit-rate ≥ threshold AND diversity ≥ min cells over the
   rolling window. The LLM may downgrade `proven` to `researching` but cannot
   upgrade `disproven` to `proven`.

When a hypothesis reaches `proven`, it **graduates**: `manager_state` flips to
`graduated`, the best child per `(asset, timeframe)` cell is flagged
`canonical=1` (cleanup-protected), and `next_revisit_at` is set.

---

## The discipline settings

Edit under **Settings → Research → Hypothesis Discipline**. Defaults are
deliberately tight; loosen only with a specific reason.

| Setting | Default | What it controls |
|---|---|---|
| `active_pool_cap` | 100 | Target population of simultaneously active hypotheses. When exceeded, the weakest active is auto-archived; operators are never refused. |
| `min_per_pick` | 2 | Strategies needed before re-picking a hypothesis. |
| `verdict_rolling_window` | 5 | How many recent child outcomes the verdict considers. |
| `verdict_hit_rate_threshold` | 0.4 | Fraction of passing children needed for `proven`. |
| `verdict_min_diversity_cells` | 2 | Distinct `(asset, timeframe)` cells needed for `proven`. |
| `revisit_interval_days` | 30 | Days until a graduated hypothesis becomes eligible to revisit. |

---

## Common operator questions

### "Why hasn't a new hypothesis appeared today?"

The pool cap is no longer a refusal gate — it's a pressure valve. Creates
always succeed under normal conditions. If no new hypothesis has appeared:

1. **The agent isn't trying.** Check `axiom-hypothesis-promotion-loop` is
   enabled and running in the scheduler.
2. **Pool-at-cap churn.** If `active_pool_cap` is too small relative to the
   rate of new hypotheses, every create evicts an older one — check the
   hypothesis log for repeated `pool at cap (N); evicted ...` lines. Either
   raise the cap or slow the create rate.
3. **Defensive pool-full refusal.** Very rare: `HypothesisPoolFullError` now
   only fires when the eviction query finds no victim. Inspect the logs for
   `hypothesis_pool_full` error codes returned from the tool path.

### "Why is one hypothesis getting all the strategies?"

It isn't, after the round-robin gate. The promotion loop's score query computes
`strategies_since_last_pick` per hypothesis and skips any hypothesis that:
- has been picked at least once (`last_dispatched_at IS NOT NULL`) AND
- has fewer than `min_per_pick` strategies since that dispatch.

If you see this complaint, look at `last_dispatched_at` and the count of
strategies created after that timestamp.

### "The promotion loop logged `no_eligible: 1` — what's that mean?"

The depth gate filtered out every candidate. This is normal, briefly: it means
every active hypothesis has been picked recently and hasn't accumulated enough
new strategies yet. If it's persistent, either:
- Workers are slow → upstream backtest queue is jammed.
- `min_per_pick` is set too high relative to actual strategy creation rate.

### "How do I force a fresh research pass on a graduated hypothesis?"

Graduated hypotheses sit in the **Graduated** tab. Open one and press the
**Revisit** button (or `POST /api/hypotheses/{id}/revisit`). This:
- Auto-evicts the weakest active hypothesis if the pool is at cap (same
  pressure valve as create_hypothesis) so the revisit always succeeds.
- Transitions the hypothesis to `manager_state='active'`,
  `status='researching'`, increments `revisit_count`, and clears
  `last_dispatched_at` so the depth gate doesn't immediately suppress it.

The agent prompt for revisited hypotheses includes a "beat the canonical"
instruction with the canonical strategies as targets to beat.

### "What does the canonical badge mean?"

A canonical strategy is the best-in-cell child of a graduated hypothesis. It
is:
- Tagged green ("Canonical") on the strategy detail header and on the
  hypothesis-detail linked-strategies list.
- Protected from `archive` and `reject` transitions even when `force=True`.

There is at most one canonical per `(asset, timeframe)` cell per hypothesis.

### "How do I tell if the verdict math floor changed an LLM verdict?"

The verdict memo records both `verdict` (final, after math floor) and
`llm_verdict` (raw LLM output). If they differ, the floor over-ruled the LLM.
The memo also stores `signals` with `mathematical_verdict`, `hit_rate`,
`diversity_cells`, etc.

### "Why is the daily revisit pass not promoting anything?"

Three checks, in order:
1. Are any hypotheses graduated with `next_revisit_at <= now`? Most aren't yet
   if `revisit_interval_days` is 30 and graduation is recent.
2. Is the active pool full? The pass stops early on cap-full and logs
   `revisit_pass.pool_full`.
3. Is the `axiom-hypothesis-revisit-pass` job enabled? It's registered in a
   disabled state by `scripts/register_hypothesis_jobs.py`.

---

## Key log lines

```
revisit_pass.pool_full active=N cap=N remaining=N      # cap stopped revisit mid-pass (batch sweep still short-circuits on cap)
revisit_pass.summary evaluated=N revisited=N skipped_pool_full=BOOL
hypothesis.graduated id=HYP-... canonicals=N demoted=N already_graduated=BOOL
promotion.no_eligible n=0 (depth gate filtered all candidates)
hypothesis pool at cap (N); evicted HYP-... (strategies=K) to admit new hypothesis
force_revisit pressure valve: evicted HYP-... to revisit HYP-...
```

---

## Rollout decision: existing hypothesis pool

With the pressure-valve model there is no triage requirement at rollout —
creates will always succeed and evict the weakest active hypothesis when the
pool is at cap. If you already have hundreds of active hypotheses, consider
raising `active_pool_cap` (default 100, max 500) to keep eviction churn low
while you catch up.

Enable:
- `axiom-hypothesis-promotion-loop` (5-min interval)
- `axiom-hypothesis-verdict-loop` (5-min interval)
- `axiom-hypothesis-revisit-pass` (daily — batch sweep still short-circuits
  when pool is full; rely on per-request force_revisit for priority revivals)
