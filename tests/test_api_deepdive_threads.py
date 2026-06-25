import pytest
from fastapi.testclient import TestClient

from axiom.db import get_db, init_db


def _seed_strategy(sid="S33001"):
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategies (id, name, type) VALUES (?, ?, ?)",
            (sid, "deepdive api test", "rsi"),
        )
        conn.commit()
    return sid


@pytest.fixture
def client(AXIOM_db):
    _seed_strategy()
    from axiom.api import app
    return TestClient(app)


def test_create_thread_returns_id_and_strategy(client):
    r = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy_id"] == "S33001"
    assert body["id"]
    assert body["id"].startswith("dd_")


def test_create_thread_is_idempotent_for_active(client):
    r1 = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    r2 = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    assert r1.json()["id"] == r2.json()["id"]


def test_archive_then_create_returns_new_id(client):
    r = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    tid = r.json()["id"]
    r2 = client.post(f"/api/deepdive/threads/{tid}/archive")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}
    r3 = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    assert r3.json()["id"] != tid


def test_archive_unknown_thread_returns_404(client):
    r = client.post("/api/deepdive/threads/dd_doesnotexist/archive")
    assert r.status_code == 404


def test_list_messages_empty_thread(client):
    r = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    tid = r.json()["id"]
    r2 = client.get(f"/api/deepdive/threads/{tid}/messages")
    assert r2.status_code == 200
    assert r2.json() == {"messages": []}


def test_list_messages_unknown_thread_returns_404(client):
    r = client.get("/api/deepdive/threads/dd_unknown/messages")
    assert r.status_code == 404


def test_list_messages_returns_persisted_rows(client):
    from axiom.deepdive_db import append_message
    r = client.post("/api/deepdive/threads", json={"strategy_id": "S33001"})
    tid = r.json()["id"]
    append_message(tid, role="user", content="hi")
    append_message(tid, role="assistant", content="hello back", model="stub")
    r2 = client.get(f"/api/deepdive/threads/{tid}/messages")
    msgs = r2.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "hello back"
    assert msgs[1]["model"] == "stub"


def test_create_thread_unknown_strategy_returns_404(client):
    r = client.post("/api/deepdive/threads", json={"strategy_id": "Z99999"})
    assert r.status_code == 404
    assert "Z99999" in r.json()["detail"]
