from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping

from fastapi import HTTPException

from axiom.db import _now, begin_phantom_recovery, get_db, get_phantom_recovery_state, get_phantom_recovery_states, mark_phantom_recovery_healed
from axiom.util import normalize_stage

log = logging.getLogger("axiom.phantom_recovery")
ACTIVE_PHANTOM_STAGES = {"gauntlet", "backtesting"}
TERMINAL_RECOVERY_STATUSES = {"healed", "exhausted"}
ACTIVE_RECOVERY_STATUSES = {"replay_running", "repair_pending", "repair_running", "final_retry_running"}
INLINE_REPLAY_STALE_AFTER = timedelta(minutes=5)
# B-31 wedge aging. Only 'replay_running' ever had a staleness escape; the
# other three active statuses could wedge FOREVER (app closed during a final
# retry, or the queued phantom_repair agent task expired/cancelled with no
# callback) — permanently occupying the sweep batch and stranding the strategy.
# A repair claim whose agent task is provably dead is finalized; a final-retry
# claim older than this window is re-driven (the in-process executor died with
# the app, so nothing else will ever finish it).
FINAL_RETRY_STALE_AFTER = timedelta(minutes=30)
REPAIR_WEDGE_MIN_AGE = timedelta(minutes=15)
# Agent-task statuses that mean the repair is still genuinely in flight.
_AGENT_TASK_ALIVE_STATUSES = {"pending", "running"}
_INLINE_PHANTOM_RECOVERY_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="Axiom-phantom-recovery",
)


def _log_phantom_future_exception(strategy_id: str, context: str):
    """H-R4: build a done_callback that logs any exception raised by a
    ThreadPoolExecutor task. Without this the future's exception is swallowed
    — phantom recoveries fail silently and look like they succeeded."""
    def _on_done(future: Future) -> None:
        try:
            exc = future.exception()
        except Exception:
            return
        if exc is not None:
            log.exception(
                "Phantom recovery %s task failed for strategy=%s",
                context,
                strategy_id,
                exc_info=exc,
            )
    return _on_done


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cooldown_is_active(value: object) -> bool:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return False
    return parsed > datetime.now(timezone.utc)


def _get_recovery_started_at(recovery_row: Mapping[str, object]) -> datetime | None:
    return (
        _parse_timestamp(recovery_row.get("last_started_at"))
        or _parse_timestamp(recovery_row.get("updated_at"))
        or _parse_timestamp(recovery_row.get("last_detected_at"))
    )


def _is_reclaimable_stale_replay_claim(recovery_row: Mapping[str, object]) -> bool:
    recovery_status = str(recovery_row.get("recovery_status") or recovery_row.get("status") or "").strip().lower()
    if recovery_status != "replay_running":
        return False
    started_at = _get_recovery_started_at(recovery_row)
    if started_at is None:
        return False
    return started_at <= datetime.now(timezone.utc) - INLINE_REPLAY_STALE_AFTER


def _stale_replay_started_before() -> str:
    return (datetime.now(timezone.utc) - INLINE_REPLAY_STALE_AFTER).isoformat()


def should_trigger_inline_phantom_recovery(strategy_row: Mapping[str, object]) -> bool:
    stage = normalize_stage(strategy_row.get("stage") or strategy_row.get("status"))
    if stage not in ACTIVE_PHANTOM_STAGES:
        return False
    if bool(strategy_row.get("has_backtest_results")):
        return False
    recovery_status = str(strategy_row.get("recovery_status") or "").strip().lower()
    if recovery_status in TERMINAL_RECOVERY_STATUSES:
        return False
    if _cooldown_is_active(strategy_row.get("recovery_cooldown_until") or strategy_row.get("cooldown_until")):
        return False
    if recovery_status in ACTIVE_RECOVERY_STATUSES:
        return _is_reclaimable_stale_replay_claim(strategy_row)
    if bool(strategy_row.get("recovery_active")):
        return False
    return True


def _schedule_allowed(strategy_id: str) -> bool:
    state = get_phantom_recovery_state(strategy_id)
    if not state:
        return True
    recovery_status = str(state.get("status") or "").strip().lower()
    if recovery_status in TERMINAL_RECOVERY_STATUSES:
        return False
    if _cooldown_is_active(state.get("cooldown_until")):
        return False
    if recovery_status in ACTIVE_RECOVERY_STATUSES:
        return _is_reclaimable_stale_replay_claim(state)
    return True


def schedule_inline_phantom_recovery(strategy_id: str, trigger: str) -> bool:
    normalized_id = str(strategy_id or "").strip()
    normalized_trigger = str(trigger or "").strip()
    if not normalized_id or not normalized_trigger:
        return False

    if not _schedule_allowed(normalized_id):
        return False

    claimed = begin_phantom_recovery(
        normalized_id,
        trigger=normalized_trigger,
        next_status="replay_running",
        require_live_phantom_eligibility=True,
        stale_replay_started_before=_stale_replay_started_before(),
    )
    if not claimed:
        return False

    try:
        fut = _INLINE_PHANTOM_RECOVERY_EXECUTOR.submit(
            submit_phantom_replay_sync,
            normalized_id,
            trigger=normalized_trigger,
        )
        fut.add_done_callback(_log_phantom_future_exception(normalized_id, f"trigger={normalized_trigger}"))
    except Exception as exc:
        mark_phantom_recovery_exhausted(normalized_id, reason=str(exc))
        return False
    return True


def _agent_task_status(agent_task_id: str) -> str | None:
    """Return the agent task's status, or None when the row is missing."""
    try:
        task_pk = int(str(agent_task_id).strip())
    except (TypeError, ValueError):
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM agent_tasks WHERE id = ?",
            (task_pk,),
        ).fetchone()
    if row is None:
        return None
    return str(row["status"] or "").strip().lower()


def _reclaim_final_retry(strategy_id: str, *, stale_before_iso: str) -> bool:
    """CAS-reclaim a wedged final_retry_running row and re-drive the replay.

    The final retry executes on the in-process executor; if the app closed
    mid-run the status stays 'final_retry_running' forever and nothing on
    restart touches it. Re-driving is faithful to the original intent (it was
    the last-chance replay, exhaust_on_failure=True), and the CAS on
    last_started_at means a re-driven retry only becomes reclaimable again
    after another full stale window."""
    now = _now()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategy_recovery_state
            SET last_started_at = ?, updated_at = ?
            WHERE strategy_id = ?
              AND status = 'final_retry_running'
              AND COALESCE(
                    julianday(last_started_at),
                    julianday(updated_at),
                    julianday(last_detected_at)
                  ) <= julianday(?)
            """,
            (now, now, strategy_id, stale_before_iso),
        )
        changes = conn.execute("SELECT changes() AS changes").fetchone()
        if int(changes["changes"] or 0) != 1:
            return False
        conn.execute(
            """
            INSERT INTO strategy_recovery_events
                (strategy_id, event_type, event_status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                "final_retry_reclaimed",
                "final_retry_running",
                json.dumps({"reason": "stale_final_retry_claim"}, separators=(",", ":"), default=str),
                now,
            ),
        )

    try:
        fut = _INLINE_PHANTOM_RECOVERY_EXECUTOR.submit(
            submit_phantom_replay_sync,
            strategy_id,
            trigger="final_retry_reclaim",
            exhaust_on_failure=True,
        )
        fut.add_done_callback(_log_phantom_future_exception(strategy_id, "final_retry_reclaim"))
    except Exception as exc:
        mark_phantom_recovery_exhausted(strategy_id, reason=f"final_retry_reclaim_submit_failed: {exc}")
        return False
    return True


def reclaim_wedged_phantom_recovery_states() -> dict[str, int]:
    """Age wedged repair/final-retry recovery states back to a usable state (B-31).

    Handles the three active statuses that previously had NO staleness escape:

    - ``repair_pending`` / ``repair_running`` whose agent task is terminal or
      missing (queue expiry cancels pending phantom_repair tasks at 2h with no
      callback; app death loses the in-process completion path) — finalized as
      'exhausted' so the row stops occupying the sweep batch forever.
    - ``final_retry_running`` older than FINAL_RETRY_STALE_AFTER (the executor
      thread died with the process) — re-driven via the inline executor.

    A short minimum age (REPAIR_WEDGE_MIN_AGE) avoids racing the in-process
    completion callbacks. Run on every sweep pass so wedged rows recover
    without operator involvement."""
    now = datetime.now(timezone.utc)
    summary = {"checked": 0, "repair_finalized": 0, "final_retry_redriven": 0}
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT strategy_id, status, active_agent_task_id,
                   last_started_at, updated_at, last_detected_at
            FROM strategy_recovery_state
            WHERE status IN ('repair_pending', 'repair_running', 'final_retry_running')
            """
        ).fetchall()

    final_retry_cutoff = (now - FINAL_RETRY_STALE_AFTER).isoformat()
    for row in rows:
        state = dict(row)
        strategy_id = str(state.get("strategy_id") or "").strip()
        status = str(state.get("status") or "").strip().lower()
        if not strategy_id:
            continue
        summary["checked"] += 1
        started_at = _get_recovery_started_at(state)
        age = (now - started_at) if started_at is not None else None
        try:
            if status == "final_retry_running":
                if age is not None and age < FINAL_RETRY_STALE_AFTER:
                    continue
                if _reclaim_final_retry(strategy_id, stale_before_iso=final_retry_cutoff):
                    summary["final_retry_redriven"] += 1
                    log.warning(
                        "phantom janitor: re-drove wedged final retry for %s (claim age %s)",
                        strategy_id, age,
                    )
                continue

            # repair_pending / repair_running
            if age is not None and age < REPAIR_WEDGE_MIN_AGE:
                continue
            agent_task_id = str(state.get("active_agent_task_id") or "").strip()
            if agent_task_id:
                task_status = _agent_task_status(agent_task_id)
                if task_status in _AGENT_TASK_ALIVE_STATUSES:
                    continue  # genuinely in flight (incl. managed retry_at backoff)
                reason = f"repair_task_lost:{task_status or 'missing'}"
            else:
                reason = "repair_task_missing"
            if mark_phantom_recovery_exhausted(strategy_id, reason=reason):
                summary["repair_finalized"] += 1
                log.warning(
                    "phantom janitor: finalized wedged %s for %s (%s)",
                    status, strategy_id, reason,
                )
        except Exception:
            log.exception("phantom janitor: failed to reclaim %s (%s)", strategy_id, status)

    if summary["repair_finalized"] or summary["final_retry_redriven"]:
        log.info("phantom janitor: %s", summary)
    return summary


def _sweep_candidate_schedulable(row: Mapping[str, object]) -> bool:
    """Cheap pre-filter so wedged/terminal recovery rows cannot occupy the
    sweep batch (the authoritative gate stays inside
    schedule_inline_phantom_recovery)."""
    status = str(row.get("recovery_status") or "").strip().lower()
    if not status:
        return True
    if status in TERMINAL_RECOVERY_STATUSES:
        return False
    if _cooldown_is_active(row.get("cooldown_until")):
        return False
    if status in ACTIVE_RECOVERY_STATUSES:
        return _is_reclaimable_stale_replay_claim(row)
    return True


def run_phantom_recovery_sweep(*, limit: int = 5) -> dict[str, int]:
    """Periodic phantom-recovery sweep for HEADLESS / autonomous operation.

    Inline phantom recovery is otherwise only triggered when a strategy ROW is
    READ (the lab UI / lifecycle endpoints). With no operator clicking around, a
    phantom strategy — stuck in gauntlet/backtesting with no canonical backtest
    result — is never recovered and stalls forever. This sweep finds a small batch
    of such strategies and schedules inline recovery for each.

    Per-strategy dedup / cooldown / already-running is enforced INSIDE
    ``schedule_inline_phantom_recovery`` (``_schedule_allowed`` + the
    ``begin_phantom_recovery`` compare-and-set), so this can run alongside the
    read-triggered path and across consecutive sweeps without double-scheduling.
    Kept to a small batch because the inline replay executor is single-worker.
    """
    # B-31: age out wedged repair/final-retry states FIRST so they either get
    # re-driven or finalized instead of permanently occupying the batch below.
    try:
        reclaim_wedged_phantom_recovery_states()
    except Exception:
        log.exception("phantom sweep: wedged-state janitor failed")

    normalized_limit = int(max(int(limit), 1))
    placeholders = ",".join("?" for _ in ACTIVE_PHANTOM_STAGES)
    stage_params = [str(stage).lower() for stage in ACTIVE_PHANTOM_STAGES]
    scanned = 0
    scheduled = 0
    skipped_ineligible = 0
    # Oversample, then filter on recovery state in Python (same predicates as
    # _schedule_allowed) — otherwise a handful of unschedulable rows at the top
    # of the updated_at ASC ordering would consume the whole batch and starve
    # every later phantom (B-31).
    fetch_limit = max(normalized_limit * 10, 50)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT s.id,
                   r.status AS recovery_status,
                   r.cooldown_until AS cooldown_until,
                   r.last_started_at AS last_started_at,
                   r.updated_at AS updated_at,
                   r.last_detected_at AS last_detected_at
            FROM strategies s
            LEFT JOIN strategy_recovery_state r ON r.strategy_id = s.id
            WHERE LOWER(TRIM(COALESCE(s.stage, s.status, ''))) IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM backtest_results br
                  WHERE br.strategy_id = s.id
                    AND (br.deleted_at IS NULL OR TRIM(COALESCE(br.deleted_at, '')) = '')
              )
            ORDER BY COALESCE(s.updated_at, s.created_at) ASC
            LIMIT ?
            """,
            (*stage_params, fetch_limit),
        ).fetchall()
    for row in rows:
        if scheduled >= normalized_limit:
            break
        candidate = dict(row)
        if not _sweep_candidate_schedulable(candidate):
            skipped_ineligible += 1
            continue
        scanned += 1
        try:
            if schedule_inline_phantom_recovery(str(candidate["id"]), "phantom_sweep"):
                scheduled += 1
        except Exception:
            log.exception("phantom sweep: failed to schedule recovery for %s", candidate["id"])
    if scheduled:
        log.info(
            "phantom sweep: scheduled inline recovery for %d/%d candidate(s) (%d ineligible skipped)",
            scheduled, scanned, skipped_ineligible,
        )
    return {"scanned": scanned, "scheduled": scheduled, "skipped_ineligible": skipped_ineligible}


def _recovery_payload_from_state(state: Mapping[str, object] | None) -> dict[str, object]:
    if not state:
        return {
            "active": False,
            "status": "idle",
            "attempt_count": 0,
            "last_error": None,
            "cooldown_until": None,
        }
    status = str(state.get("status") or "idle").strip().lower() or "idle"
    active = status in {"replay_running", "repair_pending", "repair_running", "final_retry_running"}
    return {
        "active": active,
        "status": status,
        "attempt_count": int(state.get("attempt_count") or 0),
        "last_error": state.get("last_error"),
        "cooldown_until": state.get("cooldown_until"),
        "last_started_at": state.get("last_started_at"),
        "last_detected_at": state.get("last_detected_at"),
        "updated_at": state.get("updated_at"),
    }


def build_strategy_recovery_payload(strategy_id: str) -> dict[str, object]:
    return _recovery_payload_from_state(get_phantom_recovery_state(strategy_id))


def build_strategy_recovery_payloads(strategy_ids: list[str]) -> dict[str, dict[str, object]]:
    """Batch variant — one DB query, then build payloads in-memory.

    Missing ids get the idle default (same as the single-id version)."""
    states = get_phantom_recovery_states(strategy_ids)
    return {
        str(sid or "").strip(): _recovery_payload_from_state(states.get(str(sid or "").strip()))
        for sid in strategy_ids
        if str(sid or "").strip()
    }


def _finalize_phantom_recovery_state(strategy_id: str, status: str, reason: str | None = None) -> bool:
    normalized_id = str(strategy_id or "").strip()
    normalized_status = str(status or "").strip().lower()
    normalized_reason = str(reason or "").strip() or None
    if not normalized_id or not normalized_status:
        return False

    now = _now()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategy_recovery_state
            SET status = ?,
                last_finished_at = ?,
                last_error = ?,
                active_task_id = NULL,
                active_agent_task_id = NULL,
                cooldown_until = NULL,
                updated_at = ?
            WHERE strategy_id = ?
              AND status IN ('replay_running', 'repair_pending', 'repair_running', 'final_retry_running')
            """,
            (
                normalized_status,
                now,
                normalized_reason,
                now,
                normalized_id,
            ),
        )
        changes = conn.execute("SELECT changes() AS changes").fetchone()
        if int(changes["changes"] or 0) != 1:
            return False
        conn.execute(
            """
            INSERT INTO strategy_recovery_events
                (strategy_id, event_type, event_status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_id,
                normalized_status,
                normalized_status,
                json.dumps({"reason": normalized_reason} if normalized_reason is not None else {}, separators=(",", ":"), default=str),
                now,
            ),
        )
    return True


def _assign_phantom_repair_task(strategy_id: str, reason: str) -> int:
    from axiom.brain import assign_task

    task_id = assign_task(
        "strategy-developer",
        "phantom_repair",
        f"Repair phantom strategy {strategy_id}",
        (
            "Repair this strategy so it can produce a canonical backtest result. "
            "You may edit strategy code and params. Return strict JSON with keys "
            "'repair_action', 'validation_passed', and 'repair_reason'."
        ),
        input_data={
            "strategy_id": strategy_id,
            "recovery_reason": reason,
        },
        strategy_id=strategy_id,
    )
    return int(task_id)


def mark_phantom_recovery_repair_pending(strategy_id: str, *, agent_task_id: int, reason: str) -> bool:
    normalized_id = str(strategy_id or "").strip()
    normalized_reason = str(reason or "").strip() or "repair_pending"
    normalized_agent_task_id = str(agent_task_id or "").strip()
    if not normalized_id or not normalized_agent_task_id:
        return False

    now = _now()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategy_recovery_state
            SET status = 'repair_pending',
                repair_count = repair_count + 1,
                last_finished_at = ?,
                last_error = ?,
                active_task_id = NULL,
                active_agent_task_id = ?,
                cooldown_until = NULL,
                updated_at = ?
            WHERE strategy_id = ?
              AND status IN ('replay_running', 'repair_running', 'final_retry_running')
            """,
            (
                now,
                normalized_reason,
                normalized_agent_task_id,
                now,
                normalized_id,
            ),
        )
        changes = conn.execute("SELECT changes() AS changes").fetchone()
        if int(changes["changes"] or 0) != 1:
            return False
        conn.execute(
            """
            INSERT INTO strategy_recovery_events
                (strategy_id, event_type, event_status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_id,
                "repair_queued",
                "repair_pending",
                json.dumps(
                    {
                        "reason": normalized_reason,
                        "agent_task_id": normalized_agent_task_id,
                    },
                    separators=(",", ":"),
                    default=str,
                ),
                now,
            ),
        )
    return True


def mark_phantom_recovery_repair_running(strategy_id: str, *, agent_task_id: int) -> bool:
    normalized_id = str(strategy_id or "").strip()
    normalized_agent_task_id = str(agent_task_id or "").strip()
    if not normalized_id or not normalized_agent_task_id:
        return False

    now = _now()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategy_recovery_state
            SET status = 'repair_running',
                last_started_at = ?,
                updated_at = ?
            WHERE strategy_id = ?
              AND status = 'repair_pending'
              AND active_agent_task_id = ?
            """,
            (
                now,
                now,
                normalized_id,
                normalized_agent_task_id,
            ),
        )
        changes = conn.execute("SELECT changes() AS changes").fetchone()
        if int(changes["changes"] or 0) != 1:
            return False
        conn.execute(
            """
            INSERT INTO strategy_recovery_events
                (strategy_id, event_type, event_status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_id,
                "repair_started",
                "repair_running",
                json.dumps({"agent_task_id": normalized_agent_task_id}, separators=(",", ":"), default=str),
                now,
            ),
        )
    return True


def queue_phantom_repair_task(strategy_id: str, reason: str) -> int:
    normalized_id = str(strategy_id or "").strip()
    normalized_reason = str(reason or "").strip() or "phantom_replay_failed"
    if not normalized_id:
        raise ValueError("strategy_id is required")

    task_id = _assign_phantom_repair_task(normalized_id, normalized_reason)
    if not mark_phantom_recovery_repair_pending(
        normalized_id,
        agent_task_id=task_id,
        reason=normalized_reason,
    ):
        raise RuntimeError(f"unable to mark phantom recovery repair_pending for {normalized_id}")
    return task_id


def _submit_phantom_replay_backtest(strategy_id: str) -> dict:
    from axiom.api_core import BacktestSubmitBody, post_backtest_submit

    body = BacktestSubmitBody(strategy_id=strategy_id)
    return post_backtest_submit(body, skip_auto_trash=False)


def mark_phantom_recovery_exhausted(strategy_id: str, *, reason: str) -> bool:
    return _finalize_phantom_recovery_state(strategy_id, "exhausted", reason)


def schedule_final_retry(strategy_id: str, reason: str) -> bool:
    normalized_id = str(strategy_id or "").strip()
    normalized_reason = str(reason or "").strip() or "repair_complete"
    if not normalized_id:
        return False

    now = _now()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategy_recovery_state
            SET status = 'final_retry_running',
                last_started_at = ?,
                last_finished_at = NULL,
                last_error = NULL,
                active_task_id = NULL,
                active_agent_task_id = NULL,
                cooldown_until = NULL,
                updated_at = ?
            WHERE strategy_id = ?
              AND status IN ('repair_pending', 'repair_running')
            """,
            (
                now,
                now,
                normalized_id,
            ),
        )
        changes = conn.execute("SELECT changes() AS changes").fetchone()
        if int(changes["changes"] or 0) != 1:
            return False
        conn.execute(
            """
            INSERT INTO strategy_recovery_events
                (strategy_id, event_type, event_status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_id,
                "final_retry_scheduled",
                "final_retry_running",
                json.dumps({"reason": normalized_reason}, separators=(",", ":"), default=str),
                now,
            ),
        )

    try:
        fut = _INLINE_PHANTOM_RECOVERY_EXECUTOR.submit(
            submit_phantom_replay_sync,
            normalized_id,
            trigger="phantom_repair",
            exhaust_on_failure=True,
        )
        fut.add_done_callback(_log_phantom_future_exception(normalized_id, "final_retry"))
    except Exception as exc:
        mark_phantom_recovery_exhausted(normalized_id, reason=f"{normalized_reason}; final_retry_submit_failed: {exc}")
        return False
    return True


def handle_phantom_repair_completion(strategy_id: str, repair_summary: Mapping[str, object] | dict[str, object]) -> bool:
    normalized_id = str(strategy_id or "").strip()
    summary = dict(repair_summary) if isinstance(repair_summary, Mapping) else {}
    repair_reason = str(summary.get("repair_reason") or "").strip() or "repair_failed"
    repair_action = str(summary.get("repair_action") or "").strip().lower()
    validation_passed = bool(summary.get("validation_passed"))
    if validation_passed and repair_action and repair_action != "no_fix":
        return bool(schedule_final_retry(normalized_id, repair_reason))
    return bool(mark_phantom_recovery_exhausted(normalized_id, reason=repair_reason))


def _queue_repair_or_exhaust(strategy_id: str, reason: str, *, exhaust_on_failure: bool) -> None:
    normalized_reason = str(reason or "").strip() or "phantom_replay_failed"
    if exhaust_on_failure:
        mark_phantom_recovery_exhausted(strategy_id, reason=normalized_reason)
        return
    try:
        queue_phantom_repair_task(strategy_id, normalized_reason)
    except Exception as exc:
        mark_phantom_recovery_exhausted(
            strategy_id,
            reason=f"{normalized_reason}; repair_queue_failed: {exc}",
        )


def submit_phantom_replay_sync(strategy_id: str, *, trigger: str, exhaust_on_failure: bool = False) -> None:
    normalized_id = str(strategy_id or "").strip()
    normalized_trigger = str(trigger or "").strip()
    if not normalized_id or not normalized_trigger:
        return

    try:
        response = _submit_phantom_replay_backtest(normalized_id)
    except HTTPException as exc:
        _queue_repair_or_exhaust(normalized_id, str(exc.detail or exc), exhaust_on_failure=exhaust_on_failure)
        return
    except Exception as exc:
        _queue_repair_or_exhaust(normalized_id, str(exc), exhaust_on_failure=exhaust_on_failure)
        return

    result_id = str(response.get("result_id") or "").strip() if isinstance(response, dict) else ""
    if result_id and mark_phantom_recovery_healed(normalized_id, result_id=result_id):
        return

    reason = "replay_returned_without_result_id"
    if isinstance(response, dict) and response.get("warning"):
        reason = str(response.get("warning") or reason)
    _queue_repair_or_exhaust(normalized_id, reason, exhaust_on_failure=exhaust_on_failure)


__all__ = [
    "ACTIVE_PHANTOM_STAGES",
    "build_strategy_recovery_payload",
    "handle_phantom_repair_completion",
    "mark_phantom_recovery_exhausted",
    "mark_phantom_recovery_repair_pending",
    "mark_phantom_recovery_repair_running",
    "queue_phantom_repair_task",
    "reclaim_wedged_phantom_recovery_states",
    "run_phantom_recovery_sweep",
    "schedule_final_retry",
    "schedule_inline_phantom_recovery",
    "should_trigger_inline_phantom_recovery",
    "submit_phantom_replay_sync",
]
