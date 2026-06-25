from fastapi.testclient import TestClient
from axiom.api import app


def test_shutdown_rejects_non_localhost_client():
    client = TestClient(app, client=("8.8.8.8", 12345))
    r = client.post("/api/shutdown")
    assert r.status_code == 403


def test_shutdown_accepts_localhost_and_schedules_exit(monkeypatch):
    called = {}
    monkeypatch.setattr("os._exit", lambda code: called.setdefault("code", code))
    monkeypatch.setattr("os.kill", lambda pid, sig: None)  # avoid real signal
    client = TestClient(app, client=("127.0.0.1", 12345))
    r = client.post("/api/shutdown")
    assert r.status_code == 202
    assert r.json().get("status") == "shutting_down"
