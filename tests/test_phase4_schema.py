"""Phase 4 / P4-T01 — schema migration v27 verification.

Confirms ``mcp_servers`` and ``agent_mcp_grants`` exist after migration,
that the FK ``ON DELETE CASCADE`` cleans grant rows when a server is
removed, and that the CHECK constraint on transport rejects junk values.
"""

from __future__ import annotations

import json

from axiom.db import SCHEMA_VERSION, get_db, init_db


def test_schema_version_at_least_27() -> None:
    # Phase 4 introduced v27 (mcp_servers + agent_mcp_grants). Later phases may
    # bump it further (e.g. Phase 5 -> 28 for brain_routines). The Phase 4
    # contract is satisfied as long as the migration ran, so assert >= 27.
    assert SCHEMA_VERSION >= 27


def test_mcp_servers_table_exists() -> None:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_servers'"
        ).fetchone()
        assert row is not None, "mcp_servers table not created"


def test_agent_mcp_grants_table_exists() -> None:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_mcp_grants'"
        ).fetchone()
        assert row is not None, "agent_mcp_grants table not created"


def test_transport_check_constraint() -> None:
    init_db()
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO mcp_servers (name, transport, command) VALUES (?, ?, ?)",
                ("bad", "carrier-pigeon", "echo"),
            )
            conn.commit()
            raised = False
        except Exception:
            raised = True
            conn.rollback()
        assert raised, "transport CHECK constraint should reject 'carrier-pigeon'"


def test_grant_cascade_on_server_delete() -> None:
    init_db()
    with get_db() as conn:
        # SQLite needs PRAGMA foreign_keys=ON for cascade to fire. Confirm
        # the connection has it (axiom.db sets it via PRAGMA at open).
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_on == 1, "foreign_keys pragma must be enabled"

        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, args_json) "
            "VALUES (?, 'stdio', 'echo', '[]')",
            ("cascade-test",),
        )
        conn.execute(
            "INSERT INTO agent_mcp_grants (agent_id, server_name) VALUES (?, ?)",
            ("agent-x", "cascade-test"),
        )
        conn.commit()

        before = conn.execute(
            "SELECT COUNT(*) FROM agent_mcp_grants WHERE server_name='cascade-test'"
        ).fetchone()[0]
        assert before == 1

        conn.execute("DELETE FROM mcp_servers WHERE name='cascade-test'")
        conn.commit()

        after = conn.execute(
            "SELECT COUNT(*) FROM agent_mcp_grants WHERE server_name='cascade-test'"
        ).fetchone()[0]
        assert after == 0, "grants did not cascade-delete"


def test_default_json_columns_round_trip() -> None:
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command) "
            "VALUES (?, 'stdio', 'echo')",
            ("defaults-test",),
        )
        conn.commit()
        row = conn.execute(
            "SELECT args_json, env_json, headers_json, tools_exclude_json, "
            "tools_include_json, enabled FROM mcp_servers WHERE name=?",
            ("defaults-test",),
        ).fetchone()
        assert json.loads(row["args_json"]) == []
        assert json.loads(row["env_json"]) == {}
        assert json.loads(row["headers_json"]) == {}
        assert json.loads(row["tools_exclude_json"]) == []
        assert row["tools_include_json"] is None
        assert row["enabled"] == 1
        conn.execute("DELETE FROM mcp_servers WHERE name=?", ("defaults-test",))
        conn.commit()
