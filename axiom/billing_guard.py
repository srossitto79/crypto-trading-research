"""Daily LLM spend guard for the autonomous agent loop.

Axiom runs agents in a `while True` loop that calls paid LLM APIs every round
(up to MAX_TOOL_ROUNDS per task, across an unbounded task backlog). Cost is
already estimated per task and persisted to `agent_tasks.cost_usd`
(see Axiom.cost_pricing.estimate_cost_usd + runner persistence), but until now
nothing READ that back to stop spending. A runaway agent, a large pending
backlog, or a misbehaving fallback chain could burn through an API budget with
no ceiling.

This module sums today's recorded spend and compares it against an operator-set
cap. It is intentionally:
- best-effort: any DB/parsing error returns "allowed" (never block real work on
  a telemetry failure), and a cap <= 0 means "no cap" (disabled, the default);
- cheap: one indexed aggregate over `agent_tasks` per call;
- provider-agnostic at the budget level (one shared daily pool) — the fallback
  chain can switch providers mid-task, so a per-provider cap would be leaky.

Configuration (kv key `Axiom:settings`):
- `agent_daily_cost_cap_usd`: float. Defaults to 0 = NO cap (the guard is
  opt-in). An operator who wants a ceiling sets a positive USD value; <= 0
  keeps it disabled.
"""

from __future__ import annotations

import logging

from axiom.db import get_db, kv_get
from axiom.sim.clock import get_today

log = logging.getLogger("axiom.billing_guard")

_SETTINGS_KEY = "axiom:settings"
_CAP_SETTING = "agent_daily_cost_cap_usd"
# Default: NO daily cap. This is a personal, single-operator autonomous system —
# the operator manages their own API spend and does NOT want the pipeline
# throttled by a cost ceiling. The guard stays available as an OPT-IN: set
# settings.agent_daily_cost_cap_usd to a positive USD value to enable it.
_DEFAULT_CAP_USD = 0.0


def get_daily_cost_cap() -> float:
    """Return the configured daily LLM cost cap in USD. <= 0 means no cap.

    Defaults to 0.0 (no cap) — the guard is opt-in. An explicit positive setting
    enables it; <= 0 keeps it disabled.
    """
    try:
        raw = kv_get(_SETTINGS_KEY, {})
    except Exception:
        return _DEFAULT_CAP_USD
    settings = raw if isinstance(raw, dict) else {}
    if _CAP_SETTING not in settings:
        return _DEFAULT_CAP_USD
    try:
        return max(float(settings.get(_CAP_SETTING) or 0), 0.0)
    except (TypeError, ValueError):
        return _DEFAULT_CAP_USD


def get_spend_today() -> float:
    """Sum cost_usd recorded for agent tasks today (sim-clock aware)."""
    today = get_today().isoformat()
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) AS spent
                FROM agent_tasks
                WHERE cost_usd IS NOT NULL
                  AND COALESCE(completed_at, started_at, created_at) >= ?
                """,
                (today,),
            ).fetchone()
    except Exception as exc:  # never block real work on a telemetry read
        log.debug("billing_guard: could not read spend (%s); treating as 0", exc)
        return 0.0
    if not row:
        return 0.0
    try:
        return float(row["spent"] or 0.0)
    except (TypeError, ValueError, KeyError):
        return 0.0


def check_daily_cost_cap() -> tuple[bool, str]:
    """Return (allowed, reason). allowed=False means the daily cap is reached.

    Disabled (allowed) when no positive cap is configured.
    """
    cap = get_daily_cost_cap()
    if cap <= 0:
        return True, "no cap configured"
    spent = get_spend_today()
    if spent >= cap:
        return False, (
            f"Daily LLM cost cap reached: ${spent:.2f} spent >= ${cap:.2f} cap. "
            "Agent tasks are paused until tomorrow or until the cap is raised "
            f"(settings.{_CAP_SETTING})."
        )
    return True, f"within cap: ${spent:.2f}/${cap:.2f}"


__all__ = [
    "check_daily_cost_cap",
    "get_daily_cost_cap",
    "get_spend_today",
]
