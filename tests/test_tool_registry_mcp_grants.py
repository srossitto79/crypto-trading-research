"""Phase 4 / P4-T05 — tool_registry permission-set MCP grants.

Confirms `_permission_subjects(agent_id)` reflects grants in
``agent_mcp_grants`` so that tools with permissions ``{"mcp:<server>"}``
are visible only to granted agents.

Also locks in the explicit-grant-only invariant: the `*` wildcard is
NEVER auto-added to subjects, so a tool registered with
``permissions={"mcp:foo"}`` is invisible to ungranted agents even if
those agents have other tokens (role:*, etc.).
"""

from __future__ import annotations

from axiom.agents.tool_registry import (
    _REGISTRY,
    ToolDef,
    _permission_subjects,
    get_tools_for_agent,
)
from axiom.db import get_db, init_db


def _make_agent(agent_id: str, name: str = "Test", role: str = "data-scientist") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (agent_id, name, role),
        )
        conn.commit()


def _ensure_server(name: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO mcp_servers (name, transport, command) VALUES (?, 'stdio', 'echo')",
            (name,),
        )
        conn.commit()


def _grant(agent_id: str, server: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agent_mcp_grants (agent_id, server_name) VALUES (?, ?)",
            (agent_id, server),
        )
        conn.commit()


def _revoke(agent_id: str, server: str) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM agent_mcp_grants WHERE agent_id = ? AND server_name = ?",
            (agent_id, server),
        )
        conn.commit()


def _cleanup(agent_id: str, server: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM agent_mcp_grants WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        conn.execute("DELETE FROM mcp_servers WHERE name = ?", (server,))
        conn.commit()


def test_ungranted_agent_lacks_mcp_subject() -> None:
    init_db()
    agent_id = "p4t05-ungranted-agent"
    server = "p4t05-server-a"
    _make_agent(agent_id)
    _ensure_server(server)
    try:
        subjects = _permission_subjects(agent_id)
        assert f"mcp:{server}" not in subjects
    finally:
        _cleanup(agent_id, server)


def test_granted_agent_gains_mcp_subject() -> None:
    init_db()
    agent_id = "p4t05-granted-agent"
    server = "p4t05-server-b"
    _make_agent(agent_id)
    _ensure_server(server)
    _grant(agent_id, server)
    try:
        subjects = _permission_subjects(agent_id)
        assert f"mcp:{server}" in subjects
        # original subjects still present
        assert agent_id in subjects
    finally:
        _cleanup(agent_id, server)


def test_revoking_grant_hides_subject() -> None:
    init_db()
    agent_id = "p4t05-revoke-agent"
    server = "p4t05-server-c"
    _make_agent(agent_id)
    _ensure_server(server)
    _grant(agent_id, server)
    try:
        assert f"mcp:{server}" in _permission_subjects(agent_id)
        _revoke(agent_id, server)
        assert f"mcp:{server}" not in _permission_subjects(agent_id)
    finally:
        _cleanup(agent_id, server)


def test_get_tools_for_agent_filters_mcp_visibility() -> None:
    """A tool registered with permissions={'mcp:foo'} must be invisible
    to ungranted agents and visible to granted ones."""
    init_db()
    agent_a = "p4t05-tools-a"
    agent_b = "p4t05-tools-b"
    server = "p4t05-server-d"
    tool_name = "mcp_p4t05-server-d_echo"
    _make_agent(agent_a)
    _make_agent(agent_b)
    _ensure_server(server)
    _grant(agent_a, server)

    async def _stub(_params: dict) -> str:
        return "ok"

    _REGISTRY[tool_name] = ToolDef(
        name=tool_name,
        description="MCP echo tool (test)",
        input_schema={"type": "object", "properties": {}},
        handler=_stub,
        permissions=frozenset({f"mcp:{server}"}),
    )

    try:
        names_a = {t["name"] for t in get_tools_for_agent(agent_a)}
        names_b = {t["name"] for t in get_tools_for_agent(agent_b)}
        assert tool_name in names_a, "granted agent must see the MCP tool"
        assert tool_name not in names_b, "ungranted agent must NOT see the MCP tool"
    finally:
        _REGISTRY.pop(tool_name, None)
        _cleanup(agent_a, server)
        with get_db() as conn:
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_b,))
            conn.commit()


def test_wildcard_does_not_match_mcp_subject() -> None:
    """Locks in the explicit-grant-only contract: a permission set
    containing ``*`` (e.g. inherited from a stale tool def) is unrelated
    to ``mcp:*`` and must not grant access to MCP-permissioned tools.

    A tool registered with permissions ``{"*"}`` is universally visible
    (existing behavior). A tool registered with ``{"mcp:foo"}`` is
    visible only to agents whose subject set contains ``mcp:foo`` —
    never to a hypothetical agent with ``*`` in its subjects (which
    cannot happen today by design, this is a defense-in-depth check).
    """
    init_db()
    agent_id = "p4t05-wildcard-agent"
    server = "p4t05-server-e"
    _make_agent(agent_id)
    _ensure_server(server)
    try:
        subjects = _permission_subjects(agent_id)
        assert "*" not in subjects
        # The permission set check {"mcp:foo"} & subjects must be empty
        # for an ungranted agent regardless of any other tokens.
        perms = frozenset({f"mcp:{server}"})
        assert not any(s in perms for s in subjects)
    finally:
        _cleanup(agent_id, server)
