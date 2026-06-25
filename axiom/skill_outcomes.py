"""Outcome closure cycle — decrement/increment skill confidence when a strategy
that cited the skill reaches a terminal state.

Hooked from `Axiom.brain.transition_stage` after the strategy stage commit.
Looks up the agent_tasks chain for `cited_skills` references in `output_data`,
then writes a `skill_outcome_events` row per skill and re-writes the SKILL.md
with the adjusted confidence (creating a new history row).

Brain-only: this module is invoked from Brain-side code paths only. Quant
agents do not participate in outcome closure.

Idempotency: `skill_outcome_events` has a UNIQUE index on
``(skill_name, strategy_id, triggered_by)``; re-running for the same
``(strategy_id, triggered_by)`` is a no-op.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from axiom.db import get_db
from axiom import quant_skills as qs

log = logging.getLogger("axiom.skill_outcomes")

OutcomeKind = Literal["positive", "negative", "neutral"]

# Confidence delta policy (initial — tunable).
# Spec: "paper-survival ≥21 days = positive" — positive trigger here ships as
# `live_graduated` for the synchronous hook; the async paper-survival scanner
# is deferred to a later phase.
PAPER_SURVIVAL_DAYS_POSITIVE = 21
OUTCOME_DELTA_NEGATIVE = -0.05
OUTCOME_DELTA_POSITIVE = 0.03
OUTCOME_DELTA_NEUTRAL = 0.0


def _delta_for(outcome: OutcomeKind) -> float:
    if outcome == "negative":
        return OUTCOME_DELTA_NEGATIVE
    if outcome == "positive":
        return OUTCOME_DELTA_POSITIVE
    return OUTCOME_DELTA_NEUTRAL


def _find_skills_for_strategy(strategy_id: str) -> list[tuple[str, str | None]]:
    """Return list of (skill_name, evidence_task_id) tuples cited by tasks
    linked to this strategy.

    Walks `agent_tasks` rows with matching strategy_id, parses `output_data`
    JSON, extracts `cited_skills` list. Citation field convention:
        output_data = {"cited_skills": ["regime-trend-rsi", ...], ...}

    Falls back gracefully (returns []) if no citations found — outcome closure
    is best-effort. Future ideation tasks should populate `cited_skills` to
    feed the closure loop.
    """
    cites: list[tuple[str, str | None]] = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, output_data FROM agent_tasks "
                "WHERE strategy_id = ? AND output_data IS NOT NULL "
                "ORDER BY created_at DESC",
                (strategy_id,),
            ).fetchall()
    except Exception as exc:
        log.warning("agent_tasks lookup failed for strategy %s: %s", strategy_id, exc)
        return []

    seen: set[str] = set()
    for r in rows:
        raw = r["output_data"]
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        skills = payload.get("cited_skills") if isinstance(payload, dict) else None
        if not isinstance(skills, list):
            continue
        task_id = str(r["id"])
        for s in skills:
            if not isinstance(s, str):
                continue
            sanitized = qs._sanitize_name(s)
            if sanitized in seen:
                continue
            seen.add(sanitized)
            cites.append((sanitized, task_id))
    return cites


def record_outcome(
    strategy_id: str,
    outcome: OutcomeKind,
    triggered_by: str,
    notes: str = "",
) -> list[dict]:
    """Apply outcome closure to every skill cited by the strategy's task chain.

    Writes a `skill_outcome_events` row and re-writes the SKILL.md with a new
    version per affected skill. Idempotent on
    ``(skill_name, strategy_id, triggered_by)``.

    Returns the list of inserted event dicts (empty if no citations or all
    were already recorded).
    """
    if outcome not in ("positive", "negative", "neutral"):
        raise ValueError(f"invalid outcome: {outcome!r}")

    citations = _find_skills_for_strategy(strategy_id)
    if not citations:
        return []

    delta = _delta_for(outcome)
    inserted: list[dict] = []

    for skill_name, evidence_task_id in citations:
        skill = qs.read_skill(skill_name)
        if skill is None:
            continue

        before = skill.confidence
        after = max(0.0, min(1.0, before + delta))
        actual_delta = round(after - before, 4)

        try:
            with get_db() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO skill_outcome_events "
                    "(skill_name, strategy_id, outcome, confidence_delta, "
                    "confidence_before, confidence_after, evidence_task_id, "
                    "triggered_by, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        skill_name,
                        strategy_id,
                        outcome,
                        actual_delta,
                        before,
                        after,
                        evidence_task_id,
                        triggered_by,
                        notes,
                    ),
                )
                if cur.rowcount == 0:
                    # Already recorded — skip the SKILL.md re-write to keep
                    # idempotency end-to-end.
                    continue
                event_id = cur.lastrowid
        except Exception as exc:
            log.warning("skill_outcome_events insert failed for %s: %s", skill_name, exc)
            continue

        # Apply the confidence delta and bump skill version so the change is
        # reflected in skill history.
        skill.confidence = after
        prior_version = skill.version
        skill.parent_version = prior_version
        skill.version = prior_version + 1
        skill.change_summary = f"Outcome closure: {triggered_by} ({outcome}, Δ={actual_delta:+.3f})"
        try:
            qs.write_skill(skill, evidence_task_id=evidence_task_id, created_by="skill_outcomes")
        except Exception as exc:
            log.warning("skill SKILL.md re-write failed for %s after outcome closure: %s", skill_name, exc)

        inserted.append({
            "id": event_id,
            "skill_name": skill_name,
            "strategy_id": strategy_id,
            "outcome": outcome,
            "confidence_delta": actual_delta,
            "confidence_before": before,
            "confidence_after": after,
            "evidence_task_id": evidence_task_id,
            "triggered_by": triggered_by,
            "notes": notes,
        })
        log.info(
            "Outcome closure: %s on %s (%s) Δ=%+.3f → conf=%.3f",
            skill_name, strategy_id, outcome, actual_delta, after,
        )

    return inserted


def list_skill_outcomes(
    *,
    skill_name: str | None = None,
    strategy_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Paginated query of `skill_outcome_events` ordered created_at DESC."""
    sql = (
        "SELECT id, skill_name, strategy_id, outcome, confidence_delta, "
        "confidence_before, confidence_after, evidence_task_id, "
        "triggered_by, notes, created_at "
        "FROM skill_outcome_events"
    )
    where: list[str] = []
    args: list = []
    if skill_name:
        where.append("skill_name = ?")
        args.append(qs._sanitize_name(skill_name))
    if strategy_id:
        where.append("strategy_id = ?")
        args.append(strategy_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args.extend([int(limit), int(offset)])

    try:
        with get_db() as conn:
            rows = conn.execute(sql, args).fetchall()
    except Exception as exc:
        log.warning("list_skill_outcomes failed: %s", exc)
        return []
    return [dict(r) for r in rows]
