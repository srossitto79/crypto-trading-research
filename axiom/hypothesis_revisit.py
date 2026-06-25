"""Phase 7: revisit pass for graduated hypotheses.

A daily background pass moves graduated hypotheses back to manager_state='active'
when their `next_revisit_at` has elapsed, subject to the active-pool cap. The
hypothesis status is reset to 'researching' so the verdict loop will re-evaluate
it. The agent prompt path uses `revisit_count` to switch into "beat the
canonical" mode (handled in hypothesis_promotion._dispatch_task).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from axiom.crucibles import is_crucible_protected, request_dethrone_approval
from axiom.db import get_db
from axiom.research_contract import get_hypothesis_discipline_settings

log = logging.getLogger(__name__)


def _is_protected_viable_crucible(row: Any) -> bool:
    status = str(row["status"] or "").strip().lower()
    return status == "proven" and is_crucible_protected(dict(row))


def run_revisit_pass(*, now: datetime | None = None) -> dict[str, Any]:
    """Promote graduated hypotheses whose next_revisit_at <= now back to active.

    Stops early if the active-pool cap is hit — no point continuing because
    the slot won't open mid-pass.

    Returns {revisited_ids, skipped_pool_full, evaluated, next_at_lookahead}.
    """
    discipline = get_hypothesis_discipline_settings()
    cap = int(discipline["active_pool_cap"])
    interval_days = int(discipline["revisit_interval_days"])
    moment = now or datetime.now(timezone.utc)
    moment_iso = moment.isoformat()
    next_revisit_iso = (moment + timedelta(days=interval_days)).isoformat()

    revisited: list[str] = []
    skipped_pool_full = False
    evaluated = 0

    with get_db() as conn:
        # Snapshot active count once at the top — we increment locally as we
        # promote, so the loop never has to re-query.
        active_row = conn.execute(
            "SELECT COUNT(*) AS n FROM hypotheses "
            "WHERE manager_state = 'active' "
            "AND status NOT IN ('disproven', 'proven')"
        ).fetchone()
        active_count = int(active_row["n"] or 0)

        candidates = conn.execute(
            """
            SELECT id, status, protection_status FROM hypotheses
            WHERE manager_state = 'graduated'
              AND next_revisit_at IS NOT NULL
              AND next_revisit_at <= ?
            ORDER BY next_revisit_at ASC
            """,
            (moment_iso,),
        ).fetchall()
        evaluated = len(candidates)

        for row in candidates:
            hid = str(row["id"])
            if _is_protected_viable_crucible(row):
                log.info(
                    "revisit_pass.protected_skip id=%s status=%s protection_status=%s",
                    hid,
                    row["status"],
                    row["protection_status"],
                )
                continue
            if active_count >= cap:
                skipped_pool_full = True
                log.info(
                    "revisit_pass.pool_full active=%d cap=%d remaining=%d",
                    active_count, cap, evaluated - len(revisited),
                )
                break
            conn.execute(
                """
                UPDATE hypotheses
                SET manager_state = 'active',
                    status = 'researching',
                    revisit_count = COALESCE(revisit_count, 0) + 1,
                    last_revisited_at = ?,
                    next_revisit_at = ?,
                    last_dispatched_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (moment_iso, next_revisit_iso, moment_iso, hid),
            )
            revisited.append(hid)
            active_count += 1
        conn.commit()

    log.info(
        "revisit_pass.summary evaluated=%d revisited=%d skipped_pool_full=%s",
        evaluated, len(revisited), skipped_pool_full,
    )
    return {
        "revisited_ids": revisited,
        "skipped_pool_full": skipped_pool_full,
        "evaluated": evaluated,
    }


def force_revisit(hypothesis_id: str) -> dict[str, Any]:
    """Operator-triggered revisit for one hypothesis.

    Uses the same pressure-valve semantics as create_hypothesis: when the active
    pool is at cap, the weakest active hypothesis is auto-archived to make room.
    Operators are never refused.

    Raises:
      ValueError: if the hypothesis isn't graduated.
      RuntimeError: defensive fallback if the pool is at cap AND no eviction
        victim can be found (structurally shouldn't happen).
    """
    from axiom.hypotheses import (
        _evict_hypothesis_for_pool_pressure,
        _pick_weakest_active_hypothesis,
    )

    discipline = get_hypothesis_discipline_settings()
    cap = int(discipline["active_pool_cap"])
    interval_days = int(discipline["revisit_interval_days"])
    moment = datetime.now(timezone.utc)
    moment_iso = moment.isoformat()
    next_revisit_iso = (moment + timedelta(days=interval_days)).isoformat()

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, manager_state, status, protection_status FROM hypotheses WHERE id = ?",
            (hypothesis_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown hypothesis_id: {hypothesis_id}")
        if str(row["manager_state"] or "").strip() != "graduated":
            raise ValueError(
                f"hypothesis {hypothesis_id} is in state "
                f"{row['manager_state']!r}; only 'graduated' can be revisited"
            )
        if _is_protected_viable_crucible(row):
            approval_id = request_dethrone_approval(
                hypothesis_id,
                actor="operator",
                reason="Protected crucible cannot be revisited without dethrone approval.",
                new_evidence={
                    "requested_status": "researching",
                    "requested_manager_state": "active",
                },
                recommended_action="dethrone/revisit",
                requested_status="researching",
                conn=conn,
            )
            refreshed = conn.execute(
                "SELECT manager_state, status FROM hypotheses WHERE id = ?",
                (hypothesis_id,),
            ).fetchone()
            conn.commit()
            return {
                "hypothesis_id": hypothesis_id,
                "manager_state": refreshed["manager_state"],
                "status": refreshed["status"],
                "approval_required": True,
                "approval_id": approval_id,
            }
        active_row = conn.execute(
            "SELECT COUNT(*) AS n FROM hypotheses "
            "WHERE manager_state = 'active' "
            "AND status NOT IN ('disproven', 'proven')"
        ).fetchone()
        active_count = int(active_row["n"] or 0)
        if active_count >= cap:
            victim = _pick_weakest_active_hypothesis(conn)
            if victim is None:
                raise RuntimeError(
                    f"active pool full: {active_count}/{cap} — no eviction victim"
                )
            _evict_hypothesis_for_pool_pressure(conn, victim["id"])
            log.info(
                "force_revisit pressure valve: evicted %s (strategies=%d) to revisit %s",
                victim.get("display_id") or victim["id"],
                victim["strategy_count"],
                hypothesis_id,
            )
        conn.execute(
            """
            UPDATE hypotheses
            SET manager_state = 'active',
                status = 'researching',
                revisit_count = COALESCE(revisit_count, 0) + 1,
                last_revisited_at = ?,
                next_revisit_at = ?,
                last_dispatched_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (moment_iso, next_revisit_iso, moment_iso, hypothesis_id),
        )
        conn.commit()
    return {
        "hypothesis_id": hypothesis_id,
        "manager_state": "active",
        "status": "researching",
        "next_revisit_at": next_revisit_iso,
    }
