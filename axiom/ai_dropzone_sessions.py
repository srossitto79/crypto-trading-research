"""AI Drop Zone session scoping.

A session is a lightweight grouping token the operator (or an agent) creates
before submitting strategies/backtests. Every subsequent register-file and
backtest run call can be tagged with the session_id, which makes the iteration
loop ("Claude session #7: 4 strategies, 2 survived") queryable.

Sessions are intentionally simple: a single row in ai_dropzone_sessions,
status active|closed, and tags on downstream rows. No authentication, no
ownership semantics — the operator layer above already gates access.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from axiom.db import get_db

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_session_id(conn) -> str:
    row = conn.execute(
        "SELECT id FROM ai_dropzone_sessions "
        "WHERE id LIKE 'ADZ-%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    next_num = 1
    if row:
        try:
            next_num = int(str(row["id"]).split("-", 1)[1]) + 1
        except (ValueError, IndexError):
            next_num = 1
    return f"ADZ-{next_num:04d}"


def _row_to_dict(row) -> dict[str, Any]:
    entry = dict(row)
    raw_meta = entry.pop("metadata_json", "{}")
    try:
        entry["metadata"] = json.loads(raw_meta) if raw_meta else {}
    except (json.JSONDecodeError, TypeError):
        entry["metadata"] = {}
    return entry


def create_session(
    *,
    label: str = "",
    actor: str = "",
    objective: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new active session and return its row as a dict."""
    clean_label = str(label or "").strip()[:200]
    clean_actor = str(actor or "").strip()[:80]
    clean_objective = str(objective or "").strip()[:500]
    meta_json = json.dumps(metadata or {}, separators=(",", ":"), default=str)
    started = _now_iso()
    with get_db() as conn:
        session_id = _next_session_id(conn)
        if not clean_label:
            suffix = clean_actor or "session"
            clean_label = f"{session_id} ({suffix})"
        conn.execute(
            "INSERT INTO ai_dropzone_sessions "
            "(id, label, actor, objective, status, metadata_json, started_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (session_id, clean_label, clean_actor, clean_objective, meta_json, started),
        )
        row = conn.execute(
            "SELECT * FROM ai_dropzone_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    out = _row_to_dict(row)
    out["strategy_count"] = 0
    out["run_count"] = 0
    return out


def get_session(session_id: str) -> dict[str, Any] | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM ai_dropzone_sessions WHERE id = ?", (sid,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_sessions(limit: int = 20, include_closed: bool = True) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit or 20), 100))
    with get_db() as conn:
        if include_closed:
            rows = conn.execute(
                "SELECT * FROM ai_dropzone_sessions "
                "ORDER BY started_at DESC LIMIT ?",
                (capped,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_dropzone_sessions WHERE status = 'active' "
                "ORDER BY started_at DESC LIMIT ?",
                (capped,),
            ).fetchall()
        counts = {
            r["dropzone_session_id"]: int(r["n"])
            for r in conn.execute(
                "SELECT dropzone_session_id, COUNT(*) AS n FROM strategies "
                "WHERE dropzone_session_id IS NOT NULL GROUP BY dropzone_session_id"
            ).fetchall()
        }
    out: list[dict[str, Any]] = []
    for row in rows:
        entry = _row_to_dict(row)
        entry["strategy_count"] = counts.get(entry["id"], 0)
        out.append(entry)
    return out


def close_session(session_id: str) -> dict[str, Any] | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with get_db() as conn:
        existing = conn.execute(
            "SELECT status FROM ai_dropzone_sessions WHERE id = ?", (sid,)
        ).fetchone()
        if not existing:
            return None
        if existing["status"] != "closed":
            conn.execute(
                "UPDATE ai_dropzone_sessions SET status = 'closed', ended_at = ? WHERE id = ?",
                (_now_iso(), sid),
            )
        row = conn.execute(
            "SELECT * FROM ai_dropzone_sessions WHERE id = ?", (sid,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_session_detail(session_id: str) -> dict[str, Any] | None:
    """Return a session plus the strategies tagged to it and their recent runs."""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM ai_dropzone_sessions WHERE id = ?", (sid,)
        ).fetchone()
        if not row:
            return None
        strat_rows = conn.execute(
            "SELECT id, name, type, symbol, timeframe, stage, source, source_ref, created_at "
            "FROM strategies WHERE dropzone_session_id = ? "
            "ORDER BY created_at DESC",
            (sid,),
        ).fetchall()
        strategies = [dict(r) for r in strat_rows]
        # Runs are surfaced two ways:
        #   1) backtest_results whose strategy is tagged to this session
        #      (covers the common case where the agent ran a freshly-registered
        #      strategy that carries the session tag).
        #   2) backtest_results whose config_json recorded the session_id
        #      directly (covers re-runs of older strategies from within the
        #      session — the strategy may belong to a different session but the
        #      run itself is ours).
        # A LIKE on the JSON blob is cheap enough at the query-per-session
        # cadence this endpoint is called at and avoids a schema migration on
        # the hot write path.
        strategy_ids = [s["id"] for s in strategies]
        seen_ids: set[str] = set()
        run_rows: list = []
        if strategy_ids:
            placeholders = ",".join(["?"] * len(strategy_ids))
            run_rows = conn.execute(
                f"SELECT result_id, strategy_id, symbol, timeframe, metrics_json, created_at "
                f"FROM backtest_results WHERE strategy_id IN ({placeholders}) "
                f"AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 200",
                tuple(strategy_ids),
            ).fetchall()
            seen_ids = {str(r["result_id"]) for r in run_rows}
        # Match both compact and pretty JSON forms — json.dumps uses
        # ", " / ": " separators by default, but callers may emit compact
        # blobs with no spaces after the colon.
        tagged_rows = conn.execute(
            "SELECT result_id, strategy_id, symbol, timeframe, metrics_json, created_at, config_json "
            "FROM backtest_results WHERE deleted_at IS NULL "
            "AND (config_json LIKE ? OR config_json LIKE ?) "
            "ORDER BY created_at DESC LIMIT 200",
            (
                f'%"dropzone_session_id":"{sid}"%',
                f'%"dropzone_session_id": "{sid}"%',
            ),
        ).fetchall()
        runs: list[dict[str, Any]] = []
        for r in run_rows:
            entry = dict(r)
            try:
                entry["metrics"] = json.loads(entry.pop("metrics_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                entry["metrics"] = {}
            runs.append(entry)
        for r in tagged_rows:
            rid = str(r["result_id"])
            if rid in seen_ids:
                continue
            entry = dict(r)
            entry.pop("config_json", None)
            try:
                entry["metrics"] = json.loads(entry.pop("metrics_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                entry["metrics"] = {}
            runs.append(entry)
            seen_ids.add(rid)
        runs.sort(key=lambda e: str(e.get("created_at") or ""), reverse=True)
    out = _row_to_dict(row)
    out["strategies"] = strategies
    out["runs"] = runs
    out["strategy_count"] = len(strategies)
    out["run_count"] = len(runs)
    return out


def record_strategy_in_session(
    conn,
    *,
    session_id: str | None,
    strategy_id: str,
) -> None:
    """Idempotently tag an already-created strategy row with a session_id."""
    sid = str(session_id or "").strip()
    stid = str(strategy_id or "").strip()
    if not sid or not stid:
        return
    conn.execute(
        "UPDATE strategies SET dropzone_session_id = ? "
        "WHERE id = ? AND (dropzone_session_id IS NULL OR dropzone_session_id = '')",
        (sid, stid),
    )


def session_exists(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM ai_dropzone_sessions WHERE id = ?", (sid,)
        ).fetchone()
    return bool(row)


__all__ = [
    "create_session",
    "get_session",
    "list_sessions",
    "close_session",
    "get_session_detail",
    "record_strategy_in_session",
    "session_exists",
]
