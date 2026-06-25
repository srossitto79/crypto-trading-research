import json

import pytest
from fastapi.testclient import TestClient

from axiom.db import get_db, init_db
from axiom.deepdive_db import create_or_get_active_thread


def _seed(sid="S22001"):
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategies (id, name, type) VALUES (?, ?, ?)",
            (sid, "sse test", "rsi"),
        )
        conn.commit()
    return sid


@pytest.fixture
def thread(AXIOM_db):
    sid = _seed()
    return create_or_get_active_thread(sid)


@pytest.fixture
def client():
    from axiom.api import app
    return TestClient(app)


def _parse_sse_data_lines(body: str) -> list[dict]:
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


def test_send_streams_events_in_order(thread, client, monkeypatch):
    async def fake_run_turn(thread_id, *, user_text):
        yield {"type": "user_persisted"}
        yield {"type": "assistant_token", "content": "hello"}
        yield {"type": "done", "message_id": "m1"}

    # patch the symbol as imported by the router module
    monkeypatch.setattr("axiom.routers.deepdive.run_turn", fake_run_turn)

    with client.stream("POST", f"/api/deepdive/threads/{thread['id']}/send",
                       json={"user_text": "hi"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b"".join(r.iter_bytes()).decode()

    events = _parse_sse_data_lines(body)
    assert [e["type"] for e in events] == ["user_persisted", "assistant_token", "done"]
    assert events[1]["content"] == "hello"
    assert events[2]["message_id"] == "m1"


def test_send_unknown_thread_returns_404(client, AXIOM_db):
    r = client.post("/api/deepdive/threads/dd_nope/send", json={"user_text": "hi"})
    assert r.status_code == 404


def test_send_propagates_error_event(thread, client, monkeypatch):
    async def errored_run_turn(thread_id, *, user_text):
        yield {"type": "user_persisted"}
        yield {"type": "error", "code": "cost_cap", "message": "too expensive"}

    monkeypatch.setattr("axiom.routers.deepdive.run_turn", errored_run_turn)

    with client.stream("POST", f"/api/deepdive/threads/{thread['id']}/send",
                       json={"user_text": "hi"}) as r:
        body = b"".join(r.iter_bytes()).decode()

    events = _parse_sse_data_lines(body)
    assert any(e["type"] == "error" and e.get("code") == "cost_cap" for e in events)


def test_send_to_archived_thread_returns_409(thread, client):
    from axiom.deepdive_db import archive_thread
    archive_thread(thread["id"])
    r = client.post(
        f"/api/deepdive/threads/{thread['id']}/send",
        json={"user_text": "hi"},
    )
    assert r.status_code == 409
    assert "archived" in r.json()["detail"].lower()
