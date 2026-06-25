from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from axiom.routers.webhooks import _reset_webhook_replay_cache, router as webhooks_router


def _signed_headers(secret: str, payload: dict[str, object], *, delivery_id: str) -> dict[str, str]:
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "x-hub-signature-256": signature,
        "x-github-event": "push",
        "x-github-delivery": delivery_id,
        "content-type": "application/json",
    }


def test_github_webhook_accepts_valid_push(monkeypatch):
    _reset_webhook_replay_cache()
    app = FastAPI()
    app.include_router(webhooks_router)
    client = TestClient(app)

    payload = {
        "ref": "refs/heads/main",
        "after": "abc123",
        "head_commit": {"timestamp": datetime.now(timezone.utc).isoformat()},
    }

    monkeypatch.setattr("axiom.routers.webhooks._webhook_secret", lambda: "secret")
    monkeypatch.setattr("axiom.routers.webhooks._target_branch", lambda: "main")
    monkeypatch.setattr(
        "axiom.routers.webhooks._git_pull",
        lambda **kwargs: {"fetch_output": "ok", "pull_output": "ok"},
    )
    monkeypatch.setattr("axiom.routers.webhooks._run_post_pull", lambda command, repo_path: "")

    response = client.post(
        "/api/webhooks/github",
        data=json.dumps(payload),
        headers=_signed_headers("secret", payload, delivery_id="delivery-1"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "updated"
    assert response.json()["after"] == "abc123"


def test_github_webhook_rejects_duplicate_delivery(monkeypatch):
    _reset_webhook_replay_cache()
    app = FastAPI()
    app.include_router(webhooks_router)
    client = TestClient(app)

    payload = {
        "ref": "refs/heads/main",
        "head_commit": {"timestamp": datetime.now(timezone.utc).isoformat()},
    }

    monkeypatch.setattr("axiom.routers.webhooks._webhook_secret", lambda: "secret")
    monkeypatch.setattr("axiom.routers.webhooks._target_branch", lambda: "main")
    monkeypatch.setattr(
        "axiom.routers.webhooks._git_pull",
        lambda **kwargs: {"fetch_output": "ok", "pull_output": "ok"},
    )
    monkeypatch.setattr("axiom.routers.webhooks._run_post_pull", lambda command, repo_path: "")

    headers = _signed_headers("secret", payload, delivery_id="delivery-2")
    first = client.post("/api/webhooks/github", data=json.dumps(payload), headers=headers)
    second = client.post("/api/webhooks/github", data=json.dumps(payload), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "duplicate" in second.json()["detail"].lower()


def test_github_webhook_rejects_stale_delivery(monkeypatch):
    _reset_webhook_replay_cache()
    app = FastAPI()
    app.include_router(webhooks_router)
    client = TestClient(app)

    payload = {
        "ref": "refs/heads/main",
        "head_commit": {"timestamp": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()},
    }

    monkeypatch.setattr("axiom.routers.webhooks._webhook_secret", lambda: "secret")
    monkeypatch.setattr("axiom.routers.webhooks._target_branch", lambda: "main")

    response = client.post(
        "/api/webhooks/github",
        data=json.dumps(payload),
        headers=_signed_headers("secret", payload, delivery_id="delivery-3"),
    )

    assert response.status_code == 409
    assert "replay window" in response.json()["detail"].lower()


def test_validate_git_ref_rejects_shell_metacharacters():
    """Regression for C17 — defense in depth, even though argv is used."""
    import pytest
    from axiom.routers.webhooks import _validate_git_ref

    assert _validate_git_ref("main", field="branch") == "main"
    assert _validate_git_ref("feature/quant-factory-upgrade", field="branch")
    assert _validate_git_ref("v1.2.3", field="branch") == "v1.2.3"

    for bad in ["main; rm -rf /", "main && curl evil", "main`whoami`", "main$(id)",
                "main|cat", "main\nrm", "main\trm", "", "main with space"]:
        with pytest.raises(RuntimeError, match="Invalid"):
            _validate_git_ref(bad, field="branch")


def test_validate_git_remote_rejects_shell_metacharacters():
    import pytest
    from axiom.routers.webhooks import _validate_git_remote

    assert _validate_git_remote("origin", field="remote") == "origin"
    assert _validate_git_remote("upstream-2", field="remote")

    for bad in ["origin;ls", "origin&&ls", "origin|cat", "origin/sub", "", "origin space"]:
        with pytest.raises(RuntimeError, match="Invalid"):
            _validate_git_remote(bad, field="remote")
