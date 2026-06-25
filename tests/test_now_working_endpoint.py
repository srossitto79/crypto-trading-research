"""Tests for GET /api/lab/now-working — surfaces strategies the engine is actively processing."""
from __future__ import annotations

import datetime as dt
from fastapi.testclient import TestClient

from axiom.api import app
from axiom.db import get_db, init_db
from axiom.system_pause import set_system_mode


def _seed_strategy(conn, strategy_id: str, stage: str = "gauntlet") -> None:
    conn.execute(
        """
        INSERT INTO strategies (id, name, stage, status, type, symbol, timeframe, created_at, stage_changed_at)
        VALUES (?, ?, ?, ?, 'momentum', 'BTCUSDT', '1h', datetime('now'), datetime('now'))
        """,
        (strategy_id, f"name-{strategy_id}", stage, stage),
    )


def _seed_task(
    conn,
    strategy_id: str,
    status: str,
    task_type: str = "backtest",
    started_offset_seconds: int = 0,
) -> None:
    started_at = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=started_offset_seconds)
    ).isoformat()
    conn.execute(
        """
        INSERT INTO agent_tasks (agent_id, type, strategy_id, status, created_at, started_at)
        VALUES ('test-agent', ?, ?, ?, ?, ?)
        """,
        (task_type, strategy_id, status, started_at, started_at),
    )


def test_now_working_returns_running_and_pending(tmp_path, monkeypatch):
    init_db()
    set_system_mode("auto")

    with get_db() as conn:
        _seed_strategy(conn, "S-RUN", stage="gauntlet")
        _seed_strategy(conn, "S-PEND", stage="quick_screen")
        _seed_strategy(conn, "S-DONE", stage="paper")
        _seed_strategy(conn, "S-IDLE", stage="quick_screen")
        _seed_task(conn, "S-RUN", status="running")
        _seed_task(conn, "S-PEND", status="pending")
        _seed_task(conn, "S-DONE", status="completed")
        # S-IDLE has no agent_tasks rows

    client = TestClient(app)
    resp = client.get("/api/lab/now-working")
    assert resp.status_code == 200
    body = resp.json()
    ids = {row["strategy_id"] for row in body}
    assert "S-RUN" in ids
    assert "S-PEND" in ids
    assert "S-DONE" not in ids
    assert "S-IDLE" not in ids


def test_now_working_marks_stalled_running_tasks(tmp_path, monkeypatch):
    init_db()
    set_system_mode("auto")

    with get_db() as conn:
        _seed_strategy(conn, "S-FRESH", stage="gauntlet")
        _seed_strategy(conn, "S-STALE", stage="gauntlet")
        _seed_task(conn, "S-FRESH", status="running", started_offset_seconds=10 * 60)
        _seed_task(conn, "S-STALE", status="running", started_offset_seconds=60 * 60)

    client = TestClient(app)
    resp = client.get("/api/lab/now-working")
    body = {row["strategy_id"]: row for row in resp.json()}
    assert body["S-FRESH"]["current_task"]["stalled"] is False
    assert body["S-STALE"]["current_task"]["stalled"] is True


def test_now_working_surfaces_tasks_without_strategy_id(tmp_path, monkeypatch):
    """Tasks with NULL strategy_id must still appear (LEFT JOIN behavior).

    Research, sentiment, and general agent_tasks often run without a
    strategy_id. Previously these were invisible because of an INNER JOIN.
    """
    init_db()
    set_system_mode("auto")

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO agent_tasks (agent_id, type, title, strategy_id, status, created_at, started_at)"
            " VALUES ('quant-researcher', 'general', 'Investigate BTC funding', NULL, 'running', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO agent_tasks (agent_id, type, title, strategy_id, status, created_at, started_at)"
            " VALUES ('sentiment-analyst', 'sentiment', NULL, NULL, 'pending', ?, ?)",
            (now, now),
        )

    client = TestClient(app)
    resp = client.get("/api/lab/now-working")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 2

    # Tasks without a strategy get a synthetic "task-<id>" strategy_id.
    synthetic_ids = [r for r in body if str(r["strategy_id"]).startswith("task-")]
    assert len(synthetic_ids) >= 2

    # The display name should fall back to the task title or "agent · type".
    names = {r["name"] for r in synthetic_ids}
    assert "Investigate BTC funding" in names  # title used when present
    assert any("sentiment-analyst" in n for n in names)  # agent · type used when no title

    # Stage may be None for strategy-less tasks.
    assert any(r["stage"] is None for r in synthetic_ids)


def test_now_working_in_manual_mode_excludes_system_pending_work(tmp_path, monkeypatch):
    init_db()
    set_system_mode("manual")

    with get_db() as conn:
        _seed_strategy(conn, "S-RUN", stage="gauntlet")
        _seed_strategy(conn, "S-USER", stage="quick_screen")
        _seed_strategy(conn, "S-SYSTEM", stage="quick_screen")
        _seed_task(conn, "S-RUN", status="running")
        conn.execute(
            """
            INSERT INTO agent_tasks (agent_id, type, strategy_id, status, source, created_at, started_at)
            VALUES ('test-agent', 'research', 'S-USER', 'pending', 'user', datetime('now'), datetime('now'))
            """
        )
        conn.execute(
            """
            INSERT INTO agent_tasks (agent_id, type, strategy_id, status, source, created_at, started_at)
            VALUES ('test-agent', 'research', 'S-SYSTEM', 'pending', 'system', datetime('now'), datetime('now'))
            """
        )
        conn.execute(
            """
            INSERT INTO agent_tasks (agent_id, type, strategy_id, status, source, created_at, started_at)
            VALUES ('test-agent', 'research', 'S-SYSTEM', 'paused_manual', 'system', datetime('now'), datetime('now'))
            """
        )

    client = TestClient(app)
    resp = client.get("/api/lab/now-working")
    assert resp.status_code == 200
    ids = {row["strategy_id"] for row in resp.json()}
    assert "S-RUN" in ids
    assert "S-USER" in ids
    assert "S-SYSTEM" not in ids
