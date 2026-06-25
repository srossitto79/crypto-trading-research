"""DAO for the unified in-app assistant: threads + messages.

Generalizes ``deepdive_db`` so a conversation can be scoped to a strategy, a
page/route, or nothing at all (a global helper thread). Messages carry an
explicit monotonic ``seq`` so tool_use/tool_result ordering is deterministic on
reload (the deepdive store relied on the implicit rowid via ``created_at``).

Action proposals (writes that need operator confirmation) are stored as
messages with ``role='action'`` and a ``status`` lifecycle
(``pending`` -> ``approved``/``rejected`` -> ``executed``/``failed``), so the
confirm flow needs no extra table.
"""

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
        "scope_kind": row[1],
        "scope_id": row[2],
        "page_route": row[3],
        "title": row[4],
        "created_at": row[5],
        "updated_at": row[6],
        "archived_at": row[7],
    }


_THREAD_COLS = (
    "id, scope_kind, scope_id, page_route, title, created_at, updated_at, archived_at"
)


def get_thread(thread_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            f"SELECT {_THREAD_COLS} FROM assistant_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    return _row_to_thread(row) if row else None


def create_or_get_active_thread(
    scope_kind: str | None = "global",
    scope_id: str | None = None,
    *,
    page_route: str | None = None,
) -> dict[str, Any]:
    """Return the active (non-archived) thread for a scope, creating one if none.

    ``scope_kind`` is one of ``'strategy' | 'page' | 'global'``. For a global
    helper thread pass ``scope_kind='global', scope_id=None`` — the most recent
    active global thread is reused so history survives reload. ``page_route`` is
    refreshed on reuse so we always know where the operator last used the thread.
    """
    kind = (scope_kind or "global").strip() or "global"
    sid = (scope_id or "").strip() or None
    now = _now()
    with get_db() as conn:
        if sid is None:
            row = conn.execute(
                f"SELECT {_THREAD_COLS} FROM assistant_threads "
                "WHERE scope_kind = ? AND scope_id IS NULL AND archived_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (kind,),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {_THREAD_COLS} FROM assistant_threads "
                "WHERE scope_kind = ? AND scope_id = ? AND archived_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (kind, sid),
            ).fetchone()
        if row:
            if page_route:
                conn.execute(
                    "UPDATE assistant_threads SET page_route = ?, updated_at = ? WHERE id = ?",
                    (page_route, now, row[0]),
                )
                conn.commit()
            thread = _row_to_thread(row)
            if page_route:
                thread["page_route"] = page_route
            return thread

        new_id = f"as_{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO assistant_threads "
            "(id, scope_kind, scope_id, page_route, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id, kind, sid, page_route, None, now, now),
        )
        conn.commit()
    return {
        "id": new_id,
        "scope_kind": kind,
        "scope_id": sid,
        "page_route": page_route,
        "title": None,
        "created_at": now,
        "updated_at": now,
        "archived_at": None,
    }


def archive_thread(thread_id: str) -> None:
    now = _now()
    with get_db() as conn:
        conn.execute(
            "UPDATE assistant_threads SET archived_at = ?, updated_at = ? WHERE id = ?",
            (now, now, thread_id),
        )
        conn.commit()


def _row_to_message(row) -> dict[str, Any]:
    tool_call_json = row[5]
    return {
        "id": row[0],
        "thread_id": row[1],
        "seq": row[2],
        "role": row[3],
        "content": row[4],
        "tool_call": json.loads(tool_call_json) if tool_call_json else None,
        "status": row[6],
        "created_at": row[7],
        "cost_usd": row[8],
        "model": row[9],
    }


_MSG_COLS = (
    "id, thread_id, seq, role, content, tool_call_json, status, created_at, cost_usd, model"
)


def append_message(
    thread_id: str,
    *,
    role: str,
    content: str,
    tool_call: dict | None = None,
    status: str | None = None,
    cost_usd: float | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    new_id = f"asm_{uuid.uuid4().hex[:12]}"
    now = _now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM assistant_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        seq = int(row[0] or 0) + 1
        conn.execute(
            "INSERT INTO assistant_messages "
            "(id, thread_id, seq, role, content, tool_call_json, status, created_at, cost_usd, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id, thread_id, seq, role, content,
                json.dumps(tool_call) if tool_call else None,
                status, now, cost_usd, model,
            ),
        )
        conn.execute(
            "UPDATE assistant_threads SET updated_at = ? WHERE id = ?",
            (now, thread_id),
        )
        conn.commit()
    return {
        "id": new_id,
        "thread_id": thread_id,
        "seq": seq,
        "role": role,
        "content": content,
        "tool_call": tool_call,
        "status": status,
        "created_at": now,
        "cost_usd": cost_usd,
        "model": model,
    }


def list_messages(thread_id: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT {_MSG_COLS} FROM assistant_messages "
            "WHERE thread_id = ? ORDER BY seq ASC",
            (thread_id,),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def get_message(message_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            f"SELECT {_MSG_COLS} FROM assistant_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    return _row_to_message(row) if row else None


def set_message_status(message_id: str, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE assistant_messages SET status = ? WHERE id = ?",
            (status, message_id),
        )
        conn.commit()


def thread_cost_total(thread_id: str) -> float:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM assistant_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    return float(row[0]) if row else 0.0
