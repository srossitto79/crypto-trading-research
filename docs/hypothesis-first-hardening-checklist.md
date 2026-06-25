# Hypothesis-First Hardening Checklist

Use this checklist against the live `.axiom` state after startup, after at least one fresh research cycle, and after at least one restart.

Each item should be marked as one of:
- `PASS`
- `FAIL`
- `NEEDS TUNING`

## 1. Bootstrap Sequence

- Brain creates only one bootstrap cycle after restart.
  Evidence: task log chronology, latest `brain_invoke` rows.

- Bootstrap dispatches the first research wave to enabled `strategy-developer` agents.
  Evidence: fresh task titles and agent ids in the task log.

- No first-wave bootstrap ideation task is assigned to `quant-researcher`.
  Evidence: task log chronology immediately after restart.

- Brain is not left stuck in `running` without dispatching tasks.
  Evidence: bootstrap task status and downstream agent tasks.

## 2. Ownership Boundaries

- New hypotheses are created only by `strategy-developer` role agents.
  Evidence: `/api/hypotheses`, live DB `hypotheses.origin_agent_id`, `hypotheses.origin_role`.

- Custom strategy agents normalize to `origin_role = strategy-developer`.
  Evidence: recent hypothesis payloads from `/api/hypotheses`.

- `quant-researcher` is support-only and is not generating first-class hypotheses.
  Evidence: task log, task audit log, recent hypotheses.

- Strategy creation tools are available to `strategy-developer` and not to `quant-researcher`.
  Evidence: runtime behavior plus tool-permission regression tests.

## 3. Hypothesis-to-Strategy Integrity

- Every new strategy has exactly one `hypothesis_id`.
  Evidence: `/api/strategies/.../container`, live DB `strategies.hypothesis_id`.

- Hypotheses retain stable serialized ids like `H00001` alongside raw ids.
  Evidence: `/api/hypotheses`, hypothesis detail page.

- Strategy detail shows the parent hypothesis backlink.
  Evidence: strategy detail UI and `/api/strategies/.../container`.

- External artifacts remain attached to their hypothesis and are operator-verifiable.
  Evidence: hypothesis detail page and `/api/hypotheses/{id}`.

- Data gaps can attach to hypotheses and strategies and roll up correctly.
  Evidence: hypothesis detail page and `/api/data-gaps`.

## 4. Runtime Cadence And Dwell

- Agents prefer existing viable hypotheses before minting more.
  Evidence: fresh task descriptions and recent hypothesis/strategy counts.

- New hypotheses per agent per cycle stay bounded.
  Evidence: recent task audit logs, hypothesis creation timestamps.

- Strategies per promising hypothesis show bounded follow-through.
  Evidence: per-hypothesis linked strategy counts in `/api/hypotheses/{id}`.

- Active unresolved hypotheses do not accumulate without follow-through.
  Evidence: hypothesis statuses and linked strategy counts.

- Weak or duplicate hypotheses cool down instead of endlessly multiplying.
  Evidence: recent hypothesis titles, mechanisms, and strategy fan-out.

## 5. Memory Pressure And Tunnel Vision

- Bootstrap is deterministic and not driven by saturated workspace memory.
  Evidence: startup task chronology and absence of memory-shaped bootstrap drift.

- Exploration is not collapsing into repeated RSI-heavy families by default.
  Evidence: recent hypothesis titles, mechanisms, and origin/source mix.

- Optional inspiration memory appears supportive, not directive.
  Evidence: research task outputs and task audit chronology.

- Benchmarking inputs become explicit artifacts on the hypothesis.
  Evidence: hypothesis artifact lists and source references.

## 6. Settings And Runtime Agreement

- Research lane weights in settings match the intended runtime mix.
  Evidence: `/settings -> Research`, stored settings payload, assigned lane mix over recent cycles.

- Spawn limits in settings match observed per-hypothesis strategy budgets.
  Evidence: research settings plus linked strategy counts.

- External benchmarking setting matches whether benchmarking tasks use external sources.
  Evidence: research settings, task descriptions, hypothesis artifacts.

- Agent/provider selections in the UI match the actual provider/model used at runtime.
  Evidence: task/provider metadata and agent settings.
