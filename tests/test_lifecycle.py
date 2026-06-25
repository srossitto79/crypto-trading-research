"""Tests for Axiom.lifecycle — graceful shutdown hook."""

from axiom.db import get_db
from axiom.lifecycle import mark_in_flight_tasks_interrupted
from axiom.task_progress import INTERRUPTED_STATUS


def _create_task(status: str, *, display_id: str = "T99200") -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO agent_tasks
               (agent_id, display_id, title, description, type, status, started_at)
               VALUES (?, ?, ?, '', 'general', ?, '2026-04-25T00:00:00+00:00')""",
            ("agent-test", display_id, "test", status),
        )
        return int(cursor.lastrowid)


def test_marks_running_tasks_only(AXIOM_db):
    running = _create_task("running", display_id="T99201")
    done = _create_task("done", display_id="T99202")
    pending = _create_task("pending", display_id="T99203")

    count = mark_in_flight_tasks_interrupted()
    assert count == 1

    with get_db() as conn:
        statuses = {
            r["id"]: r["status"]
            for r in conn.execute(
                "SELECT id, status FROM agent_tasks WHERE id IN (?, ?, ?)",
                (running, done, pending),
            ).fetchall()
        }
    assert statuses[running] == INTERRUPTED_STATUS
    assert statuses[done] == "done"
    assert statuses[pending] == "pending"


def test_idempotent_second_call_is_no_op(AXIOM_db):
    _create_task("running", display_id="T99204")
    first = mark_in_flight_tasks_interrupted()
    assert first == 1
    second = mark_in_flight_tasks_interrupted()
    assert second == 0


def test_no_running_tasks_returns_zero(AXIOM_db):
    _create_task("done", display_id="T99205")
    assert mark_in_flight_tasks_interrupted() == 0


def test_does_not_touch_interrupted_tasks(AXIOM_db):
    """A task already marked interrupted (from a prior shutdown) should not be re-marked."""
    task_id = _create_task("running", display_id="T99206")
    mark_in_flight_tasks_interrupted()  # → interrupted

    # Subsequent call should not touch this row.
    count = mark_in_flight_tasks_interrupted()
    assert count == 0

    with get_db() as conn:
        status = conn.execute(
            "SELECT status FROM agent_tasks WHERE id = ?", (task_id,)
        ).fetchone()["status"]
    assert status == INTERRUPTED_STATUS
