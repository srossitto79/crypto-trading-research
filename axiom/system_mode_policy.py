from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from axiom.db import get_db
from axiom.system_pause import MODE_MANUAL, MODE_SEMI_AUTO, get_system_mode

log = logging.getLogger(__name__)

PENDING_STATUS = "pending"
RUNNING_STATUS = "running"
PAUSED_MANUAL_STATUS = "paused_manual"
USER_SOURCE = "user"
SYSTEM_SOURCE = "system"


def normalize_task_source(source: object) -> str:
    normalized = str(source or SYSTEM_SOURCE).strip().lower()
    return normalized or SYSTEM_SOURCE


def is_operator_source(source: object) -> bool:
    return normalize_task_source(source) == USER_SOURCE


def is_manual_mode(mode: str | None = None) -> bool:
    return _normalize_system_mode(mode) == MODE_MANUAL


def _normalize_system_mode(mode: str | None = None) -> str:
    return str(mode or get_system_mode()).strip().lower().replace("-", "_")


def is_semi_auto_mode(mode: str | None = None) -> bool:
    return _normalize_system_mode(mode) == MODE_SEMI_AUTO


def autonomous_hardening_allowed(mode: str | None = None) -> bool:
    return not is_manual_mode(mode)


def autonomous_hypothesis_generation_allowed(mode: str | None = None) -> bool:
    return _normalize_system_mode(mode) not in {MODE_MANUAL, MODE_SEMI_AUTO}


def autonomous_runtime_allowed(mode: str | None = None) -> bool:
    return autonomous_hardening_allowed(mode)


def initial_queue_status_for_source(source: object, *, mode: str | None = None) -> str:
    if is_manual_mode(mode) and not is_operator_source(source):
        return PAUSED_MANUAL_STATUS
    return PENDING_STATUS


def task_source_is_claimable(source: object, *, mode: str | None = None) -> bool:
    if not is_manual_mode(mode):
        return True
    return is_operator_source(source)


def get_paused_manual_counts() -> dict[str, int]:
    with get_db() as conn:
        agent_tasks = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM agent_tasks WHERE status = ?",
                (PAUSED_MANUAL_STATUS,),
            ).fetchone()["n"]
        )
        brain_tasks = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status = ?",
                (PAUSED_MANUAL_STATUS,),
            ).fetchone()["n"]
        )
    return {
        "agent_tasks": agent_tasks,
        "brain_tasks": brain_tasks,
        "total": agent_tasks + brain_tasks,
    }


def freeze_manual_mode_backlog() -> dict[str, int]:
    with get_db() as conn:
        agent_result = conn.execute(
            """
            UPDATE agent_tasks
               SET status = ?
             WHERE status = ?
               AND COALESCE(source, ?) <> ?
            """,
            (PAUSED_MANUAL_STATUS, PENDING_STATUS, SYSTEM_SOURCE, USER_SOURCE),
        )
        brain_result = conn.execute(
            """
            UPDATE tasks
               SET status = ?
             WHERE status = ?
               AND COALESCE(source, ?) <> ?
            """,
            (PAUSED_MANUAL_STATUS, PENDING_STATUS, SYSTEM_SOURCE, USER_SOURCE),
        )
    return {
        "agent_tasks": int(agent_result.rowcount or 0),
        "brain_tasks": int(brain_result.rowcount or 0),
        "total": int(agent_result.rowcount or 0) + int(brain_result.rowcount or 0),
    }


def thaw_manual_mode_backlog() -> dict[str, int]:
    with get_db() as conn:
        now = datetime.now(timezone.utc).isoformat()
        active_rows = conn.execute(
            """
            SELECT strategy_id, type
              FROM agent_tasks
             WHERE status IN (?, ?)
               AND strategy_id IS NOT NULL
               AND TRIM(strategy_id) <> ''
            """,
            (PENDING_STATUS, RUNNING_STATUS),
        ).fetchall()
        active_keys = {
            (str(row["strategy_id"]).strip(), str(row["type"]).strip())
            for row in active_rows
        }
        paused_rows = conn.execute(
            """
            SELECT id, strategy_id, type
              FROM agent_tasks
             WHERE status = ?
               AND COALESCE(source, ?) <> ?
             ORDER BY priority DESC, created_at DESC, id DESC
            """,
            (PAUSED_MANUAL_STATUS, SYSTEM_SOURCE, USER_SOURCE),
        ).fetchall()

        thawed_agent_tasks = 0
        skipped_duplicate_agent_tasks = 0
        seen_keys = set(active_keys)
        for row in paused_rows:
            strategy_id = str(row["strategy_id"] or "").strip()
            task_type = str(row["type"] or "").strip()
            key = (strategy_id, task_type) if strategy_id and task_type else None
            if key is not None and key in seen_keys:
                conn.execute(
                    """
                    UPDATE agent_tasks
                       SET status = 'cancelled',
                           error = ?,
                           completed_at = ?
                     WHERE id = ?
                    """,
                    (
                        "Superseded by active task during auto-mode thaw",
                        now,
                        int(row["id"]),
                    ),
                )
                skipped_duplicate_agent_tasks += 1
                continue
            conn.execute(
                "UPDATE agent_tasks SET status = ?, error = NULL WHERE id = ?",
                (PENDING_STATUS, int(row["id"])),
            )
            thawed_agent_tasks += 1
            if key is not None:
                seen_keys.add(key)

        brain_result = conn.execute(
            """
            UPDATE tasks
               SET status = ?
             WHERE status = ?
               AND COALESCE(source, ?) <> ?
            """,
            (PENDING_STATUS, PAUSED_MANUAL_STATUS, SYSTEM_SOURCE, USER_SOURCE),
        )
    return {
        "agent_tasks": thawed_agent_tasks,
        "brain_tasks": int(brain_result.rowcount or 0),
        "total": thawed_agent_tasks + int(brain_result.rowcount or 0),
        "skipped_duplicate_agent_tasks": skipped_duplicate_agent_tasks,
    }


def reconcile_manual_mode_backlog() -> dict[str, int]:
    if is_manual_mode():
        freeze_manual_mode_backlog()
        return get_paused_manual_counts()
    counts = get_paused_manual_counts()
    if counts.get("total", 0) > 0:
        thaw_manual_mode_backlog()
        return get_paused_manual_counts()
    return counts


def sync_manual_mode_transition(*, previous_mode: str | None, current_mode: str | None) -> dict[str, int]:
    previous_is_manual = is_manual_mode(previous_mode)
    current_is_manual = is_manual_mode(current_mode)
    if not previous_is_manual and current_is_manual:
        freeze_manual_mode_backlog()
    elif previous_is_manual and not current_is_manual:
        thaw_result = thaw_manual_mode_backlog()
        skipped = int(thaw_result.get("skipped_duplicate_agent_tasks", 0) or 0)
        if skipped > 0:
            log.info(
                "manual->auto thaw: thawed agent_tasks=%d brain_tasks=%d, "
                "cancelled %d superseded duplicate agent task(s)",
                int(thaw_result.get("agent_tasks", 0) or 0),
                int(thaw_result.get("brain_tasks", 0) or 0),
                skipped,
            )
    return get_paused_manual_counts()


def manual_mode_counts_payload() -> dict[str, Any]:
    counts = get_paused_manual_counts()
    return {"paused_manual_counts": counts}


__all__ = [
    "PAUSED_MANUAL_STATUS",
    "PENDING_STATUS",
    "RUNNING_STATUS",
    "SYSTEM_SOURCE",
    "USER_SOURCE",
    "autonomous_hardening_allowed",
    "autonomous_hypothesis_generation_allowed",
    "autonomous_runtime_allowed",
    "freeze_manual_mode_backlog",
    "get_paused_manual_counts",
    "initial_queue_status_for_source",
    "is_manual_mode",
    "is_operator_source",
    "is_semi_auto_mode",
    "manual_mode_counts_payload",
    "normalize_task_source",
    "reconcile_manual_mode_backlog",
    "sync_manual_mode_transition",
    "task_source_is_claimable",
    "thaw_manual_mode_backlog",
]
