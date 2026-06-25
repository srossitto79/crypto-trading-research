"""H-O1: agent_tasks.audit_log accumulates state transition events."""

from __future__ import annotations

import json

import pytest

from axiom.db import append_task_audit_event, get_db, init_db


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


@pytest.fixture
def seeded_task():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO agent_tasks (agent_id, type, title, description, input_data, status, assigned_by, priority, source, created_at, audit_log) "
            "VALUES ('test-agent', 'test', 'title', 'd', '{}', 'pending', 'brain', 0, 'test', '2026-04-16T00:00:00+00:00', '[]')"
        )
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        task_id = int(row["id"])
    yield task_id
    with get_db() as conn:
        conn.execute("DELETE FROM agent_tasks WHERE id = ?", (task_id,))


def _read_audit(task_id: int) -> list[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT audit_log FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
    return json.loads(row["audit_log"]) if row and row["audit_log"] else []


def test_append_single_event(seeded_task):
    with get_db() as conn:
        append_task_audit_event(conn, seeded_task, "failed", {"error_type": "RuntimeError"})
    events = _read_audit(seeded_task)
    assert len(events) == 1
    assert events[0]["event"] == "failed"
    assert events[0]["error_type"] == "RuntimeError"
    assert "timestamp" in events[0]


def test_append_preserves_ordering(seeded_task):
    with get_db() as conn:
        append_task_audit_event(conn, seeded_task, "claimed")
        append_task_audit_event(conn, seeded_task, "running", {"runner": "headless"})
        append_task_audit_event(conn, seeded_task, "done", {"tokens": 123})
    events = _read_audit(seeded_task)
    assert [e["event"] for e in events] == ["claimed", "running", "done"]
    assert events[1]["runner"] == "headless"
    assert events[2]["tokens"] == 123


def test_append_is_noop_for_unknown_task():
    """No exception when the task_id does not exist."""
    with get_db() as conn:
        append_task_audit_event(conn, 9_999_999, "ghost-event")  # must not raise


def test_append_is_noop_for_falsy_id():
    with get_db() as conn:
        append_task_audit_event(conn, 0, "nope")
        append_task_audit_event(conn, None, "nope")  # type: ignore[arg-type]


def test_append_recovers_from_malformed_audit_log(seeded_task):
    """If audit_log is corrupted to a non-list value, the helper resets
    to a fresh list rather than crashing."""
    with get_db() as conn:
        conn.execute("UPDATE agent_tasks SET audit_log = 'garbage' WHERE id = ?", (seeded_task,))
        append_task_audit_event(conn, seeded_task, "recovered")
    events = _read_audit(seeded_task)
    assert len(events) == 1 and events[0]["event"] == "recovered"


def test_details_do_not_overwrite_core_fields(seeded_task):
    """User-supplied keys in `details` cannot overwrite event/timestamp."""
    with get_db() as conn:
        append_task_audit_event(
            conn, seeded_task, "failed",
            {"event": "lies", "timestamp": "fake", "error": "real"},
        )
    events = _read_audit(seeded_task)
    assert events[0]["event"] == "failed"
    assert events[0]["timestamp"] != "fake"
    assert events[0]["error"] == "real"
