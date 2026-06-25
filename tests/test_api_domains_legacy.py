from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from axiom.routers.legacy import router as legacy_router


def test_legacy_AXIOM_get_routes_dashboard(monkeypatch):
    monkeypatch.setattr(
        "axiom.api_domains.legacy.control_plane_status.get_dashboard",
        lambda require_account_connection=True: {"execution_mode": "paper", "daemon_running": True},
    )

    app = FastAPI()
    app.include_router(legacy_router)
    client = TestClient(app)

    response = client.get("/api/Axiom/dashboard")

    assert response.status_code == 200
    assert response.json()["execution_mode"] == "paper"
    assert response.headers["Deprecation"] == "true"
    assert "Sunset" in response.headers


def test_legacy_model_policy_route_delegates(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_put_legacy_model_policy(body):
        captured["provider_priority"] = body.provider_priority
        return {"ok": True}

    monkeypatch.setattr("axiom.routers.legacy.legacy_domain.put_legacy_model_policy", _fake_put_legacy_model_policy)

    app = FastAPI()
    app.include_router(legacy_router)
    client = TestClient(app)

    response = client.put("/api/Axiom/model-policy", json={"provider_priority": ["openai", "minimax"]})

    assert response.status_code == 200
    assert captured["provider_priority"] == ["openai", "minimax"]
    assert response.headers["Deprecation"] == "true"


def test_legacy_agent_patch_route_delegates(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_patch(agent_id: str, body):
        captured["agent_id"] = agent_id
        captured["name"] = body.name
        return {"ok": True}

    monkeypatch.setattr("axiom.routers.legacy.legacy_domain.legacy_patch_agent", _fake_patch)

    app = FastAPI()
    app.include_router(legacy_router)
    client = TestClient(app)

    response = client.patch("/api/Axiom/agents/brain", json={"name": "Brain 2"})

    assert response.status_code == 200
    assert captured == {"agent_id": "brain", "name": "Brain 2"}


def test_legacy_websocket_mount_delegates(monkeypatch):
    async def _fake_legacy_websocket_endpoint(ws: WebSocket):
        await ws.accept()
        await ws.send_text("legacy-ok")
        await ws.close()

    monkeypatch.setattr(
        "axiom.routers.legacy.legacy_domain.legacy_websocket_endpoint",
        _fake_legacy_websocket_endpoint,
    )

    app = FastAPI()
    app.include_router(legacy_router)
    client = TestClient(app)

    with client.websocket_connect("/api/Axiom/ws/live") as websocket:
        assert websocket.receive_text() == "legacy-ok"
