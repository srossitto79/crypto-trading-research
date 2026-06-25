"""Phase 4 / P4-T08 — diagnostics snapshot includes mcp_servers section."""

from __future__ import annotations


from axiom.db import get_db, init_db
from axiom.diagnostics import snapshot


def test_snapshot_has_mcp_servers_key() -> None:
    init_db()
    snap = snapshot()
    assert "mcp_servers" in snap
    assert isinstance(snap["mcp_servers"], list)


def test_snapshot_lists_known_server_with_status() -> None:
    init_db()
    name = "p4t08-snapshot"
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, enabled, "
            "last_status, last_status_at, last_error) VALUES "
            "(?, 'stdio', 'echo', 1, 'ok', '2026-04-25T00:00:00+00:00', NULL)",
            (name,),
        )
        conn.commit()

    try:
        snap = snapshot()
        names = {r["name"]: r for r in snap["mcp_servers"]}
        assert name in names
        row = names[name]
        assert row["transport"] == "stdio"
        assert row["enabled"] is True
        assert row["last_status"] == "ok"
        assert row["last_error_short"] is None
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
            conn.commit()


def test_snapshot_truncates_long_error() -> None:
    init_db()
    name = "p4t08-long-err"
    long_err = "x" * 500
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, enabled, "
            "last_status, last_error) VALUES (?, 'stdio', 'echo', 1, 'error', ?)",
            (name, long_err),
        )
        conn.commit()

    try:
        snap = snapshot()
        row = next((r for r in snap["mcp_servers"] if r["name"] == name), None)
        assert row is not None
        # Truncated to 120 chars + ellipsis (120 + 1 = 121).
        assert row["last_error_short"] is not None
        assert len(row["last_error_short"]) <= 122
        assert row["last_error_short"].endswith("…")
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
            conn.commit()
