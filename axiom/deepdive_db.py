"""DAO for Deepdive threads and messages."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from axiom.db import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_thread(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "strategy_id": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "archived_at": row[4],
    }


def get_thread(thread_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, strategy_id, created_at, updated_at, archived_at "
            "FROM deepdive_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    return _row_to_thread(row) if row else None


def create_or_get_active_thread(strategy_id: str) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, strategy_id, created_at, updated_at, archived_at "
            "FROM deepdive_threads "
            "WHERE strategy_id = ? AND archived_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        if row:
            return _row_to_thread(row)
        new_id = f"dd_{uuid.uuid4().hex[:12]}"
        now = _now()
        conn.execute(
            "INSERT INTO deepdive_threads (id, strategy_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id, strategy_id, now, now),
        )
        conn.commit()
    return {
        "id": new_id,
        "strategy_id": strategy_id,
        "created_at": now,
        "updated_at": now,
        "archived_at": None,
    }


def archive_thread(thread_id: str) -> None:
    now = _now()
    with get_db() as conn:
        conn.execute(
            "UPDATE deepdive_threads SET archived_at = ?, updated_at = ? WHERE id = ?",
            (now, now, thread_id),
        )
        conn.commit()


def _row_to_message(row) -> dict[str, Any]:
    tool_call_json = row[4]
    return {
        "id": row[0],
        "thread_id": row[1],
        "role": row[2],
        "content": row[3],
        "tool_call": json.loads(tool_call_json) if tool_call_json else None,
        "created_at": row[5],
        "cost_usd": row[6],
        "model": row[7],
    }


def append_message(
    thread_id: str,
    *,
    role: str,
    content: str,
    tool_call: dict | None = None,
    cost_usd: float | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    new_id = f"ddm_{uuid.uuid4().hex[:12]}"
    now = _now()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO deepdive_messages "
            "(id, thread_id, role, content, tool_call_json, created_at, cost_usd, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id, thread_id, role, content,
                json.dumps(tool_call) if tool_call else None,
                now, cost_usd, model,
            ),
        )
        conn.execute(
            "UPDATE deepdive_threads SET updated_at = ? WHERE id = ?",
            (now, thread_id),
        )
        conn.commit()
    return {
        "id": new_id,
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "tool_call": tool_call,
        "created_at": now,
        "cost_usd": cost_usd,
        "model": model,
    }


def list_messages(thread_id: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, thread_id, role, content, tool_call_json, created_at, cost_usd, model "
            "FROM deepdive_messages WHERE thread_id = ? ORDER BY created_at ASC",
            (thread_id,),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def thread_cost_total(thread_id: str) -> float:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM deepdive_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    return float(row[0]) if row else 0.0
