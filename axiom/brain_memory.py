"""Brain operational memory — Phase 1 (P1-T02).

Backs the singleton `brain_memory` row plus an append-only `brain_memory_history`
audit log. Brain-only: quant agents stay stateless; this is the Brain agent's
persistent operational notes (loaded into the cacheable user-message prefix
each cycle by P1-T04).

Hard rules:
- `MAX_MEMORY_CHARS = 2000` — enforced at the application layer, never silently
  truncated. Cap violations raise `BrainMemoryTooLargeError`.
- Every mutation writes a `brain_memory_history` row in the same transaction
  as the `brain_memory` UPDATE.
- `before_excerpt` and `after_excerpt` capture the first 200 chars of the body
  before/after the mutation so the audit log stays bounded.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from axiom.db import get_db

MAX_MEMORY_CHARS = 2000
_EXCERPT_CHARS = 200
_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')"


class BrainMemoryTooLargeError(ValueError):
    """Raised when a mutation would push the body past `MAX_MEMORY_CHARS`."""

    def __init__(self, current_len: int, attempted_len: int, cap: int = MAX_MEMORY_CHARS) -> None:
        super().__init__(
            f"brain memory cap exceeded: attempted {attempted_len} chars (current {current_len}, cap {cap})"
        )
        self.current_len = current_len
        self.attempted_len = attempted_len
        self.cap = cap


def _excerpt(body: str) -> str:
    return (body or "")[:_EXCERPT_CHARS]


def _read_body(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT body FROM brain_memory WHERE id = 1").fetchone()
    if row is None:
        return ""
    body = row["body"] if isinstance(row, sqlite3.Row) else row[0]
    return body or ""


def _write_body(
    conn: sqlite3.Connection,
    *,
    new_body: str,
    before_body: str,
    mutation_type: str,
    mutated_by: str | None,
) -> None:
    conn.execute(
        f"UPDATE brain_memory SET body = ?, updated_at = {_NOW_SQL}, updated_by = ? WHERE id = 1",
        (new_body, mutated_by),
    )
    conn.execute(
        "INSERT INTO brain_memory_history "
        "(mutation_type, before_excerpt, after_excerpt, mutated_by) "
        "VALUES (?, ?, ?, ?)",
        (mutation_type, _excerpt(before_body), _excerpt(new_body), mutated_by),
    )


def get_memory() -> str:
    """Return the current memory body."""
    with get_db() as conn:
        return _read_body(conn)


def get_memory_with_meta() -> dict[str, Any]:
    """Return body plus updated_at / updated_by metadata."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT body, updated_at, updated_by FROM brain_memory WHERE id = 1"
        ).fetchone()
        if row is None:
            return {
                "body": "",
                "updated_at": None,
                "updated_by": None,
                "char_count": 0,
                "cap": MAX_MEMORY_CHARS,
            }
        body = row["body"] or ""
        return {
            "body": body,
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
            "char_count": len(body),
            "cap": MAX_MEMORY_CHARS,
        }


def set_memory(
    new_body: str,
    *,
    mutated_by: str | None,
    mutation_type: str = "replace",
) -> dict[str, Any]:
    """Full overwrite. Validates the cap, writes one history row."""
    body = new_body or ""
    if len(body) > MAX_MEMORY_CHARS:
        # Cap check happens before opening a write transaction so we don't
        # leave the connection mid-txn on a guaranteed-failing input.
        with get_db() as conn:
            current = _read_body(conn)
        raise BrainMemoryTooLargeError(len(current), len(body))

    with get_db() as conn:
        before = _read_body(conn)
        _write_body(
            conn,
            new_body=body,
            before_body=before,
            mutation_type=mutation_type,
            mutated_by=mutated_by,
        )
    return {"ok": True, "char_count": len(body), "cap": MAX_MEMORY_CHARS}


def add_memory(addition: str, *, mutated_by: str | None) -> dict[str, Any]:
    """Append `addition` to the existing body with a newline separator.

    Cumulative length is checked atomically inside the write transaction so
    concurrent appends can't both squeeze past the cap.
    """
    add = addition or ""
    if not add:
        return {"ok": True, "char_count": len(get_memory()), "cap": MAX_MEMORY_CHARS, "noop": True}

    with get_db() as conn:
        # Take the write lock immediately so two concurrent add_memory callers
        # serialize cleanly on the cap check.
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = _read_body(conn)
            joined = f"{before}\n{add}" if before else add
            if len(joined) > MAX_MEMORY_CHARS:
                conn.execute("ROLLBACK")
                raise BrainMemoryTooLargeError(len(before), len(joined))
            _write_body(
                conn,
                new_body=joined,
                before_body=before,
                mutation_type="add",
                mutated_by=mutated_by,
            )
            conn.execute("COMMIT")
        except BrainMemoryTooLargeError:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"ok": True, "char_count": len(joined), "cap": MAX_MEMORY_CHARS}


def remove_memory_section(needle: str, *, mutated_by: str | None) -> dict[str, Any]:
    """Remove the first occurrence of `needle`. No-op if not present."""
    target = needle or ""
    if not target:
        return {"ok": False, "reason": "empty_needle"}

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = _read_body(conn)
            idx = before.find(target)
            if idx < 0:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "not_found"}
            new_body = before[:idx] + before[idx + len(target):]
            _write_body(
                conn,
                new_body=new_body,
                before_body=before,
                mutation_type="remove",
                mutated_by=mutated_by,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"ok": True, "char_count": len(new_body), "cap": MAX_MEMORY_CHARS}


def list_history(limit: int = 20) -> list[dict[str, Any]]:
    """Return up to `limit` most recent history rows, newest first."""
    bounded = max(1, min(int(limit), 200))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, mutation_type, before_excerpt, after_excerpt, mutated_at, mutated_by "
            "FROM brain_memory_history ORDER BY mutated_at DESC, id DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "mutation_type": row["mutation_type"],
            "before_excerpt": row["before_excerpt"],
            "after_excerpt": row["after_excerpt"],
            "mutated_at": row["mutated_at"],
            "mutated_by": row["mutated_by"],
        }
        for row in rows
    ]


__all__ = [
    "MAX_MEMORY_CHARS",
    "BrainMemoryTooLargeError",
    "add_memory",
    "get_memory",
    "get_memory_with_meta",
    "list_history",
    "remove_memory_section",
    "set_memory",
]
