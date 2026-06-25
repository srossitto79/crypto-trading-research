"""Phase 4 / P4-T07 — /api/mcp/* router tests.

Exercises CRUD on `mcp_servers` and `agent_mcp_grants`, and confirms
``POST /api/mcp/servers/{name}/test`` surfaces a clear error message
when the configured command is bogus.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from axiom.api_security import require_operator_access
from axiom.db import get_db, init_db
from axiom.routers.mcp import router as mcp_router


FAKE_SERVER = Path(__file__).parent / "fake_mcp_stdio_server.py"


@pytest.fixture
def client():
    init_db()
    app = FastAPI()
    app.include_router(mcp_router)
    app.dependency_overrides[require_operator_access] = lambda: None
    with TestClient(app) as c:
        yield c


def _cleanup(name: str | None = None, agent: str | None = None) -> None:
    with get_db() as conn:
        if name:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        if agent:
            conn.execute("DELETE FROM agents WHERE id = ?", (agent,))
        conn.commit()


def test_create_list_get_delete_roundtrip(client):
    name = "p4t07-rt"
    _cleanup(name=name)
    try:
        # Create — disabled to avoid running the server during create.
        r = client.post("/api/mcp/servers", json={
            "name": name,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "enabled": False,
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == name
        assert body["transport"] == "stdio"
        assert body["enabled"] is False

        # Duplicate -> 409
        r = client.post("/api/mcp/servers", json={
            "name": name,
            "transport": "stdio",
            "command": "echo",
        })
        assert r.status_code == 409

        # List
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["servers"]}
        assert name in names

        # Get
        r = client.get(f"/api/mcp/servers/{name}")
        assert r.status_code == 200
        assert r.json()["name"] == name

        # Get missing -> 404
        r = client.get("/api/mcp/servers/does-not-exist-zzz")
        assert r.status_code == 404

        # Delete
        r = client.delete(f"/api/mcp/servers/{name}")
        assert r.status_code == 204

        # Get after delete -> 404
        r = client.get(f"/api/mcp/servers/{name}")
        assert r.status_code == 404
    finally:
        _cleanup(name=name)


def test_create_validation_rejects_bad_transport(client):
    r = client.post("/api/mcp/servers", json={
        "name": "p4t07-bad-transport",
        "transport": "carrier-pigeon",
        "command": "echo",
    })
    assert r.status_code == 400
    assert "transport" in r.json()["detail"].lower()


def test_create_validation_stdio_requires_command(client):
    r = client.post("/api/mcp/servers", json={
        "name": "p4t07-no-cmd",
        "transport": "stdio",
    })
    assert r.status_code == 400
    assert "command" in r.json()["detail"].lower()


def test_create_validation_http_requires_url(client):
    r = client.post("/api/mcp/servers", json={
        "name": "p4t07-no-url",
        "transport": "http",
    })
    assert r.status_code == 400
    assert "url" in r.json()["detail"].lower()


def test_test_endpoint_surfaces_clear_error(client):
    name = "p4t07-bad-test"
    _cleanup(name=name)
    try:
        r = client.post("/api/mcp/servers", json={
            "name": name,
            "transport": "stdio",
            "command": "this-binary-does-not-exist-12345",
            "enabled": False,
        })
        assert r.status_code == 201
        r = client.post(f"/api/mcp/servers/{name}/test")
        # Must NOT 500 — must return ok=false with an error string.
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"]
    finally:
        _cleanup(name=name)


def test_test_endpoint_404_when_server_missing(client):
    r = client.post("/api/mcp/servers/p4t07-not-here/test")
    assert r.status_code == 404


def test_test_endpoint_succeeds_against_fake_server(client):
    name = "p4t07-test-ok"
    _cleanup(name=name)
    try:
        r = client.post("/api/mcp/servers", json={
            "name": name,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "enabled": False,
        })
        assert r.status_code == 201
        r = client.post(f"/api/mcp/servers/{name}/test")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["protocol_version"] == "2024-11-05"
        assert body["server_info"]["name"] == "fake-stdio"
    finally:
        _cleanup(name=name)


def test_list_server_tools_returns_live_list(client):
    name = "p4t07-tools"
    _cleanup(name=name)
    try:
        client.post("/api/mcp/servers", json={
            "name": name,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "enabled": False,
        })
        r = client.get(f"/api/mcp/servers/{name}/tools")
        assert r.status_code == 200
        names = {t["name"] for t in r.json()["tools"]}
        assert names == {"echo", "uppercase"}
    finally:
        _cleanup(name=name)


def test_grants_lifecycle(client):
    server = "p4t07-grants-srv"
    agent = "p4t07-grants-agent"
    _cleanup(name=server, agent=agent)
    try:
        # Set up server + agent
        client.post("/api/mcp/servers", json={
            "name": server,
            "transport": "stdio",
            "command": "echo",
            "enabled": False,
        })
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
                (agent, "T07 Agent", "data-scientist"),
            )
            conn.commit()

        # No grants yet
        r = client.get(f"/api/mcp/agents/{agent}/grants")
        assert r.status_code == 200
        assert r.json()["grants"] == []

        # Grant
        r = client.post(
            f"/api/mcp/agents/{agent}/grants",
            json={"server_name": server},
        )
        assert r.status_code == 201

        # Listed
        r = client.get(f"/api/mcp/agents/{agent}/grants")
        assert r.status_code == 200
        grants = r.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["server_name"] == server

        # Re-grant is idempotent (INSERT OR IGNORE)
        r = client.post(
            f"/api/mcp/agents/{agent}/grants",
            json={"server_name": server},
        )
        assert r.status_code == 201

        # Revoke
        r = client.delete(f"/api/mcp/agents/{agent}/grants/{server}")
        assert r.status_code == 204

        # Revoke missing -> 404
        r = client.delete(f"/api/mcp/agents/{agent}/grants/{server}")
        assert r.status_code == 404

        # Grant for non-existent server -> 404
        r = client.post(
            f"/api/mcp/agents/{agent}/grants",
            json={"server_name": "no-such-server-zzz"},
        )
        assert r.status_code == 404

        # Grant for non-existent agent -> 404
        r = client.post(
            "/api/mcp/agents/no-such-agent-zzz/grants",
            json={"server_name": server},
        )
        assert r.status_code == 404
    finally:
        _cleanup(name=server, agent=agent)


def test_delete_cascades_grants(client):
    """Deleting a server must cascade-remove its grants (FK ON DELETE CASCADE)."""
    server = "p4t07-cascade-srv"
    agent = "p4t07-cascade-agent"
    _cleanup(name=server, agent=agent)
    try:
        client.post("/api/mcp/servers", json={
            "name": server, "transport": "stdio", "command": "echo", "enabled": False,
        })
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents (id, name, role) VALUES (?, ?, ?)",
                (agent, "Cascade", "data-scientist"),
            )
            conn.commit()
        client.post(f"/api/mcp/agents/{agent}/grants", json={"server_name": server})

        client.delete(f"/api/mcp/servers/{server}")

        r = client.get(f"/api/mcp/agents/{agent}/grants")
        assert r.json()["grants"] == []
    finally:
        _cleanup(name=server, agent=agent)


def test_update_toggling_enabled_re_registers(client):
    """Updating enabled=True should re-register tools; enabled=False unregisters."""
    name = "p4t07-toggle"
    _cleanup(name=name)
    try:
        # Created enabled=False -> tools not registered.
        client.post("/api/mcp/servers", json={
            "name": name,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "enabled": False,
        })
        from axiom.agents.tool_registry import _REGISTRY
        assert not any(k.startswith(f"mcp_{name}_") for k in _REGISTRY)

        # Enable -> tools register
        r = client.put(f"/api/mcp/servers/{name}", json={"enabled": True})
        assert r.status_code == 200
        assert any(k.startswith(f"mcp_{name}_") for k in _REGISTRY)

        # Disable -> tools unregister
        r = client.put(f"/api/mcp/servers/{name}", json={"enabled": False})
        assert r.status_code == 200
        assert not any(k.startswith(f"mcp_{name}_") for k in _REGISTRY)
    finally:
        _cleanup(name=name)
