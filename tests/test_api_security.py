from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from axiom.api_security import ApiKeyMiddleware, get_allowed_cors_origins, require_operator_access


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/shutdown")
    def shutdown_route() -> dict[str, str]:
        return {"status": "shutting_down"}

    @app.get("/api/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/operator", dependencies=[Depends(require_operator_access)])
    def operator() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_api_key_middleware_exempts_health(monkeypatch):
    monkeypatch.setenv("AXIOM_API_KEY", "api-key-123")
    client = TestClient(_build_test_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_key_middleware_requires_api_key(monkeypatch):
    monkeypatch.setenv("AXIOM_API_KEY", "api-key-123")
    client = TestClient(_build_test_app())

    missing = client.get("/api/ping")
    allowed = client.get("/api/ping", headers={"X-API-Key": "api-key-123"})

    assert missing.status_code == 401
    assert missing.json()["detail"] == "Invalid or missing API key"
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}


def test_operator_routes_require_operator_key_when_configured(monkeypatch):
    monkeypatch.setenv("AXIOM_API_KEY", "api-key-123")
    monkeypatch.setenv("AXIOM_OPERATOR_KEY", "operator-key-456")
    client = TestClient(_build_test_app())

    missing = client.post("/api/operator", headers={"X-API-Key": "api-key-123"})
    allowed = client.post(
        "/api/operator",
        headers={
            "X-API-Key": "api-key-123",
            "X-Operator-Key": "operator-key-456",
        },
    )

    assert missing.status_code == 401
    assert missing.json()["detail"] == "Invalid or missing operator key"
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}


def test_api_key_middleware_exempts_shutdown(monkeypatch):
    """A local launcher POSTs /api/shutdown on close without the per-launch key;
    the route has its own 127.0.0.1-only check so skipping auth is safe."""
    monkeypatch.setenv("AXIOM_API_KEY", "api-key-123")
    client = TestClient(_build_test_app())

    response = client.post("/api/shutdown")

    assert response.status_code == 200
    assert response.json() == {"status": "shutting_down"}


def test_allowed_cors_origins_default_to_explicit_local_hosts(monkeypatch):
    monkeypatch.delenv("AXIOM_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("FRONTEND_PORT", "4173")
    monkeypatch.setenv("AXIOM_PORT", "9003")

    origins = get_allowed_cors_origins()

    assert "http://127.0.0.1:4173" in origins
    assert "http://localhost:4173" in origins
    assert "*" not in origins
