"""Tests for Axiom.task_progress — checkpointing and resumable tasks."""

import time

import pytest

from axiom.db import get_db
from axiom.task_progress import (
    INTERRUPTED_STATUS,
    checkpoint,
    clear_checkpoints,
    list_checkpoints,
    list_resumable_tasks,
    mark_interrupted,
    read_checkpoint,
    resume_task,
)


def _create_task(*, agent_id: str = "agent-test", status: str = "running",
                 display_id: str = "T99100", title: str = "test task") -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO agent_tasks
               (agent_id, display_id, title, description, type, status, started_at)
               VALUES (?, ?, ?, '', 'general', ?, ?)""",
            (agent_id, display_id, title, status, "2026-04-25T00:00:00+00:00"),
        )
        return int(cursor.lastrowid)


def test_checkpoint_and_read_roundtrip(AXIOM_db):
    task_id = _create_task(display_id="T99101")
    checkpoint(task_id, "symbols_processed", ["BTCUSDT", "ETHUSDT"])
    payload = read_checkpoint(task_id, "symbols_processed")
    assert payload == ["BTCUSDT", "ETHUSDT"]


def test_checkpoint_upserts_on_conflict(AXIOM_db):
    task_id = _create_task(display_id="T99102")
    checkpoint(task_id, "progress", {"step": 1})
    checkpoint(task_id, "progress", {"step": 2})
    assert read_checkpoint(task_id, "progress") == {"step": 2}
    # Only one row.
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM task_checkpoints WHERE task_id = ?", (task_id,)
        ).fetchone()["c"]
    assert count == 1


def test_read_missing_returns_none(AXIOM_db):
    task_id = _create_task(display_id="T99103")
    assert read_checkpoint(task_id, "nope") is None


def test_invalid_task_id_raises(AXIOM_db):
    with pytest.raises(ValueError):
        checkpoint(0, "k", "v")
    with pytest.raises(ValueError):
        checkpoint(-1, "k", "v")


def test_invalid_key_raises(AXIOM_db):
    task_id = _create_task(display_id="T99104")
    with pytest.raises(ValueError):
        checkpoint(task_id, "", "v")


def test_list_checkpoints_orders_by_updated_desc(AXIOM_db):
    task_id = _create_task(display_id="T99105")
    checkpoint(task_id, "early", "first")
    time.sleep(0.01)
    checkpoint(task_id, "late", "second")
    rows = list_checkpoints(task_id)
    assert len(rows) == 2
    assert rows[0]["key"] == "late"
    assert rows[1]["key"] == "early"


def test_clear_checkpoints(AXIOM_db):
    task_id = _create_task(display_id="T99106")
    checkpoint(task_id, "a", 1)
    checkpoint(task_id, "b", 2)
    removed = clear_checkpoints(task_id)
    assert removed == 2
    assert list_checkpoints(task_id) == []


def test_mark_interrupted_flips_only_running(AXIOM_db):
    running_id = _create_task(display_id="T99107", status="running")
    done_id = _create_task(display_id="T99108", status="done")
    failed_id = _create_task(display_id="T99109", status="failed")

    count = mark_interrupted()
    assert count == 1

    with get_db() as conn:
        statuses = {
            r["id"]: r["status"]
            for r in conn.execute(
                "SELECT id, status FROM agent_tasks WHERE id IN (?, ?, ?)",
                (running_id, done_id, failed_id),
            ).fetchall()
        }
    assert statuses[running_id] == INTERRUPTED_STATUS
    assert statuses[done_id] == "done"
    assert statuses[failed_id] == "failed"


def test_mark_interrupted_explicit_ids(AXIOM_db):
    a = _create_task(display_id="T99110", status="running")
    b = _create_task(display_id="T99111", status="running")
    count = mark_interrupted([a])
    assert count == 1
    with get_db() as conn:
        rows = {r["id"]: r["status"] for r in conn.execute(
            "SELECT id, status FROM agent_tasks WHERE id IN (?, ?)", (a, b)
        ).fetchall()}
    assert rows[a] == INTERRUPTED_STATUS
    assert rows[b] == "running"


def test_mark_interrupted_empty_list_no_op(AXIOM_db):
    _create_task(display_id="T99112", status="running")
    assert mark_interrupted([]) == 0


def test_list_resumable_tasks_returns_only_interrupted(AXIOM_db):
    task_id = _create_task(display_id="T99113", status="running")
    _create_task(display_id="T99114", status="done")
    checkpoint(task_id, "progress", {"completed": 50})
    mark_interrupted([task_id])

    resumable = list_resumable_tasks()
    assert len(resumable) == 1
    entry = resumable[0]
    assert entry["id"] == task_id
    assert entry["display_id"] == "T99113"
    assert entry["checkpoint_count"] == 1
    assert entry["latest_checkpoint"]["key"] == "progress"
    assert entry["latest_checkpoint"]["payload"] == {"completed": 50}


def test_resume_task_flips_status_to_pending(AXIOM_db):
    task_id = _create_task(display_id="T99115", status="running")
    mark_interrupted([task_id])
    assert resume_task(task_id) is True
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, started_at, completed_at, error FROM agent_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert row["status"] == "pending"
    assert row["started_at"] is None
    assert row["completed_at"] is None
    assert row["error"] is None


def test_resume_task_only_works_on_interrupted(AXIOM_db):
    """A 'done' task should not be flipped to pending by resume_task."""
    task_id = _create_task(display_id="T99116", status="done")
    assert resume_task(task_id) is False


def test_resume_task_preserves_checkpoints(AXIOM_db):
    task_id = _create_task(display_id="T99117", status="running")
    checkpoint(task_id, "k", "value")
    mark_interrupted([task_id])
    resume_task(task_id)
    assert read_checkpoint(task_id, "k") == "value"


def test_complex_payload_roundtrip(AXIOM_db):
    """Nested dict/list/None should survive JSON roundtrip."""
    task_id = _create_task(display_id="T99118")
    payload = {
        "nested": {"a": [1, 2, 3], "b": None},
        "items": [{"id": "x"}, {"id": "y"}],
        "flag": True,
    }
    checkpoint(task_id, "complex", payload)
    assert read_checkpoint(task_id, "complex") == payload
