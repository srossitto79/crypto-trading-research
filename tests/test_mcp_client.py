"""Phase 4 / P4-T04 — MCP client integration tests.

Exercises the full handshake → list → call → close flow against a fake
stdio MCP server (``tests/fake_mcp_stdio_server.py``). Also validates:
- env scrubbing strips DYLD_*/LD_PRELOAD even from caller-supplied env_json,
- DB load/record helpers persist transport status.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from axiom.agents import mcp_client
from axiom.agents.mcp_client import (
    MCPProtocolError,
    MCPServerConfig,
    call_tool,
    close,
    connect,
    list_tools,
    load_server_config,
    record_status,
)
from axiom.db import get_db, init_db


FAKE_SERVER = Path(__file__).parent / "fake_mcp_stdio_server.py"


def _fake_stdio_config(name: str = "fake-stdio") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(FAKE_SERVER)],
        env={},
    )


def test_scrub_user_env_drops_forbidden_keys() -> None:
    raw = {
        "MCP_TOKEN": "secret",
        "LD_PRELOAD": "/tmp/evil.so",
        "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
        "DYLD_LIBRARY_PATH": "/tmp",
        "LD_LIBRARY_PATH": "/tmp",
        "LD_AUDIT": "/tmp/audit.so",
        "MY_SAFE_VAR": "ok",
    }
    cleaned = mcp_client._scrub_user_env(raw)
    assert "MCP_TOKEN" in cleaned
    assert "MY_SAFE_VAR" in cleaned
    assert "LD_PRELOAD" not in cleaned
    assert "DYLD_INSERT_LIBRARIES" not in cleaned
    assert "DYLD_LIBRARY_PATH" not in cleaned
    assert "LD_LIBRARY_PATH" not in cleaned
    assert "LD_AUDIT" not in cleaned


def test_full_handshake_list_call_close() -> None:
    async def run():
        config = _fake_stdio_config()
        session = await connect(config)
        try:
            assert session.server_protocol_version == "2024-11-05"
            assert session.server_info.get("name") == "fake-stdio"

            tools = await list_tools(session)
            tool_names = {t["name"] for t in tools}
            assert tool_names == {"echo", "uppercase"}

            echoed = await call_tool(session, "echo", {"text": "hello"})
            assert echoed == "hello"

            upper = await call_tool(session, "uppercase", {"text": "hello"})
            assert upper == "HELLO"
        finally:
            await close(session)

        # Subprocess actually terminated.
        assert session.proc is None

    asyncio.run(run())


def test_call_unknown_tool_returns_protocol_error() -> None:
    async def run():
        config = _fake_stdio_config()
        session = await connect(config)
        try:
            with pytest.raises(MCPProtocolError) as exc:
                await call_tool(session, "does-not-exist", {})
            assert "unknown tool" in str(exc.value) or "unknown method" in str(exc.value)
        finally:
            await close(session)

    asyncio.run(run())


def test_connect_failure_records_error_status() -> None:
    """Bad command -> connect raises and records status='error'."""
    init_db()
    name = "bad-server-test"
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, args_json) "
            "VALUES (?, 'stdio', ?, ?)",
            (name, "this-binary-does-not-exist-12345", json.dumps([])),
        )
        conn.commit()

    async def run():
        config = MCPServerConfig(
            name=name,
            transport="stdio",
            command="this-binary-does-not-exist-12345",
            args=[],
        )
        with pytest.raises((FileNotFoundError, MCPProtocolError, OSError)):
            await connect(config)

    try:
        asyncio.run(run())
    finally:
        with get_db() as conn:
            row = conn.execute(
                "SELECT last_status, last_error FROM mcp_servers WHERE name = ?",
                (name,),
            ).fetchone()
            assert row is not None
            assert row["last_status"] == "error"
            assert row["last_error"]  # populated, contents may vary by OS
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
            conn.commit()


def test_load_server_config_round_trip() -> None:
    init_db()
    name = "load-test-server"
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command, args_json, env_json, "
            "tools_exclude_json, enabled) VALUES (?, 'stdio', ?, ?, ?, ?, 1)",
            (
                name,
                "echo",
                json.dumps(["a", "b"]),
                json.dumps({"FOO": "bar"}),
                json.dumps(["dangerous_tool"]),
            ),
        )
        conn.commit()

    try:
        cfg = load_server_config(name)
        assert cfg is not None
        assert cfg.name == name
        assert cfg.transport == "stdio"
        assert cfg.command == "echo"
        assert cfg.args == ["a", "b"]
        assert cfg.env == {"FOO": "bar"}
        assert cfg.tools_exclude == ["dangerous_tool"]
        assert cfg.tools_include is None
        assert cfg.enabled is True
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
            conn.commit()


def test_load_server_config_missing_returns_none() -> None:
    init_db()
    assert load_server_config("definitely-does-not-exist-zzz") is None


def test_record_status_updates_columns() -> None:
    init_db()
    name = "status-test-server"
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command) VALUES (?, 'stdio', 'echo')",
            (name,),
        )
        conn.commit()

    try:
        record_status(name, ok=True)
        with get_db() as conn:
            row = conn.execute(
                "SELECT last_status, last_error FROM mcp_servers WHERE name = ?",
                (name,),
            ).fetchone()
            assert row["last_status"] == "ok"
            assert row["last_error"] is None

        record_status(name, ok=False, error="boom")
        with get_db() as conn:
            row = conn.execute(
                "SELECT last_status, last_error FROM mcp_servers WHERE name = ?",
                (name,),
            ).fetchone()
            assert row["last_status"] == "error"
            assert row["last_error"] == "boom"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
            conn.commit()
