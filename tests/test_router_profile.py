"""Phase 6 / P6-T04 — /api/profile router tests."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from axiom.api_security import require_operator_access
from axiom.routers.profile import router as profile_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(profile_router)
    app.dependency_overrides[require_operator_access] = lambda: None
    with TestClient(app) as c:
        yield c


def test_get_empty_profile(client):
    r = client.get("/api/profile")
    assert r.status_code == 200
    body = r.json()
    # _isolate_AXIOM_home creates a workspace dir, but USER.md is absent
    # so exists may be False. If a default template is auto-installed, exists
    # might be True with body content.
    assert "exists" in body
    assert "structured" in body
    assert "body" in body


def test_put_then_get_roundtrip(client):
    payload = {
        "structured": {
            "name": "Trader",
            "timezone": "UTC",
            "risk_per_trade_pct": 1.5,
            "preferences": {
                "risk_appetite": "conservative",
                "response_style": "terse",
            },
            "rules": ["rule one", "rule two"],
        },
        "body": "extra prose",
    }
    r = client.put("/api/profile", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] is True
    s = body["structured"]
    assert s["name"] == "Trader"
    assert s["risk_per_trade_pct"] == 1.5
    assert s["preferences"]["risk_appetite"] == "conservative"
    assert s["rules"] == ["rule one", "rule two"]
    assert "extra prose" in body["body"]

    # Re-fetch
    r2 = client.get("/api/profile")
    assert r2.status_code == 200
    s2 = r2.json()["structured"]
    assert s2["name"] == "Trader"


def test_put_invalid_risk_appetite_rejected(client):
    r = client.put(
        "/api/profile",
        json={"structured": {"preferences": {"risk_appetite": "yolo"}}},
    )
    assert r.status_code == 422


def test_put_partial_update_preserves_unset_fields(client):
    client.put("/api/profile", json={"structured": {"name": "Trader", "timezone": "UTC"}})
    r = client.put("/api/profile", json={"structured": {"timezone": "America/Chicago"}})
    assert r.status_code == 200
    s = r.json()["structured"]
    assert s["name"] == "Trader"  # preserved
    assert s["timezone"] == "America/Chicago"  # updated


def test_put_body_only(client):
    r = client.put("/api/profile", json={"body": "just notes"})
    assert r.status_code == 200
    assert r.json()["body"].strip() == "just notes"
