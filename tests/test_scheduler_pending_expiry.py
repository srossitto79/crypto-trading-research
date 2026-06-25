from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from axiom import scheduler


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_expire_old_pending_tasks_respects_fresh_brain_callbacks(monkeypatch, tmp_path):
    db_path = tmp_path / "scheduler-expiry.db"

    with _connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE agent_tasks (
                id INTEGER PRIMARY KEY,
                status TEXT,
                created_at TEXT,
                completed_at TEXT,
                error TEXT,
                retry_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                type TEXT,
                status TEXT,
                priority INTEGER,
                created_at TEXT,
                completed_at TEXT,
                error TEXT,
                retry_at TEXT
            )
            """
        )
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        stale = (now - timedelta(minutes=180)).strftime("%Y-%m-%d %H:%M:%S")
        future_retry = (now + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO tasks (id, type, status, priority, created_at, completed_at, error, retry_at) VALUES (1, 'brain_invoke', 'pending', 1, ?, NULL, NULL, NULL)",
            (fresh,),
        )
        conn.execute(
            "INSERT INTO tasks (id, type, status, priority, created_at, completed_at, error, retry_at) VALUES (2, 'brain_invoke', 'pending', 1, ?, NULL, NULL, NULL)",
            (stale,),
        )
        # Stale by created_at but under managed retry (future retry_at) — must
        # survive the reaper: it is intentionally waiting, not stuck.
        conn.execute(
            "INSERT INTO tasks (id, type, status, priority, created_at, completed_at, error, retry_at) VALUES (3, 'brain_invoke', 'pending', 1, ?, NULL, NULL, ?)",
            (stale, future_retry),
        )
        conn.commit()

    monkeypatch.setattr(scheduler, "get_db", lambda: _connect(str(db_path)))

    scheduler._expire_old_pending_tasks()

    with _connect(str(db_path)) as conn:
        rows = conn.execute("SELECT id, status, error FROM tasks ORDER BY id").fetchall()

    assert dict(rows[0]) == {"id": 1, "status": "pending", "error": None}
    assert dict(rows[1])["status"] == "cancelled"
    assert dict(rows[1])["error"] == "Expired: pending too long"
    # Managed-retry task (future retry_at) is exempt from the stale-pending reaper.
    assert dict(rows[2])["status"] == "pending"
    assert dict(rows[2])["error"] is None
