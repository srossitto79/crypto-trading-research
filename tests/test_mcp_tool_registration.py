"""Phase 4 / P4-T06 — Dynamic MCP tool registration into tool_registry.

Confirms `register_server_tools` exposes a fake stdio MCP server's tools
to granted agents through `get_tools_for_agent`, that ungranted agents
do not see them, and that `unregister_server_tools` cleans them up.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from axiom.agents.mcp_client import (
    register_server_tools,
    unregister_server_tools,
)
from axiom.agents.tool_registry import _REGISTRY, get_tools_for_agent
from axiom.db import get_db, init_db


FAKE_SERVER = Path(__file__).parent / "fake_mcp_stdio_server.py"


def _seed(server: str, agent_granted: str, agent_other: str) -> None:
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (server,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, args_json, enabled) "
            "VALUES (?, 'stdio', ?, ?, 1)",
            (server, sys.executable, json.dumps([str(FAKE_SERVER)])),
        )
        conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (agent_granted, "Granted", "data-scientist"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (agent_other, "Other", "data-scientist"),
        )
        conn.execute("DELETE FROM agent_mcp_grants WHERE server_name = ?", (server,))
        conn.execute(
            "INSERT INTO agent_mcp_grants (agent_id, server_name) VALUES (?, ?)",
            (agent_granted, server),
        )
        conn.commit()


def _cleanup(server: str, *agents: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM agent_mcp_grants WHERE server_name = ?", (server,))
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (server,))
        for a in agents:
            conn.execute("DELETE FROM agents WHERE id = ?", (a,))
        conn.commit()


def test_register_exposes_tools_to_granted_agent_only() -> None:
    server = "p4t06-fake"
    granted = "p4t06-granted"
    other = "p4t06-other"
    _seed(server, granted, other)

    try:
        count = asyncio.run(register_server_tools(server))
        assert count == 2, f"expected 2 registered tools, got {count}"

        names = set(_REGISTRY.keys())
        assert f"mcp_{server}_echo" in names
        assert f"mcp_{server}_uppercase" in names

        granted_tools = {t["name"] for t in get_tools_for_agent(granted)}
        other_tools = {t["name"] for t in get_tools_for_agent(other)}

        assert f"mcp_{server}_echo" in granted_tools
        assert f"mcp_{server}_uppercase" in granted_tools
        assert f"mcp_{server}_echo" not in other_tools
        assert f"mcp_{server}_uppercase" not in other_tools
    finally:
        unregister_server_tools(server)
        _cleanup(server, granted, other)


def test_unregister_removes_all_tools_for_server() -> None:
    server = "p4t06-unreg"
    granted = "p4t06-unreg-agent"
    _seed(server, granted, granted + "-other")

    try:
        asyncio.run(register_server_tools(server))
        assert any(k.startswith(f"mcp_{server}_") for k in _REGISTRY)

        removed = unregister_server_tools(server)
        assert removed == 2
        assert not any(k.startswith(f"mcp_{server}_") for k in _REGISTRY)

        # Idempotent: second call removes 0
        assert unregister_server_tools(server) == 0
    finally:
        unregister_server_tools(server)
        _cleanup(server, granted, granted + "-other")


def test_disabled_server_skipped() -> None:
    server = "p4t06-disabled"
    granted = "p4t06-disabled-agent"
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (server,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, args_json, enabled) "
            "VALUES (?, 'stdio', ?, ?, 0)",
            (server, sys.executable, json.dumps([str(FAKE_SERVER)])),
        )
        conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (granted, "Granted", "data-scientist"),
        )
        conn.commit()

    try:
        count = asyncio.run(register_server_tools(server))
        assert count == 0
        assert not any(k.startswith(f"mcp_{server}_") for k in _REGISTRY)
    finally:
        _cleanup(server, granted)


def test_missing_server_returns_zero() -> None:
    init_db()
    count = asyncio.run(register_server_tools("does-not-exist-p4t06"))
    assert count == 0


def test_include_exclude_filter() -> None:
    server = "p4t06-filtered"
    granted = "p4t06-filtered-agent"
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (server,))
        # Exclude 'uppercase' — only 'echo' should register.
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, args_json, "
            "tools_exclude_json, enabled) VALUES (?, 'stdio', ?, ?, ?, 1)",
            (
                server,
                sys.executable,
                json.dumps([str(FAKE_SERVER)]),
                json.dumps(["uppercase"]),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (granted, "Granted", "data-scientist"),
        )
        conn.execute("DELETE FROM agent_mcp_grants WHERE server_name = ?", (server,))
        conn.execute(
            "INSERT INTO agent_mcp_grants (agent_id, server_name) VALUES (?, ?)",
            (granted, server),
        )
        conn.commit()

    try:
        count = asyncio.run(register_server_tools(server))
        assert count == 1
        names = {t["name"] for t in get_tools_for_agent(granted)}
        assert f"mcp_{server}_echo" in names
        assert f"mcp_{server}_uppercase" not in names
    finally:
        unregister_server_tools(server)
        _cleanup(server, granted)
