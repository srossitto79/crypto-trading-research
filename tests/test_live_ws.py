from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from axiom import api_core
from axiom.api_domains import live_ws
from axiom.routers.websockets import router as websockets_router


def test_live_websocket_emits_keepalive_ping(monkeypatch):
    monkeypatch.setattr(live_ws, "WS_TICK_SECONDS", 0.01)
    monkeypatch.setattr(live_ws, "WS_PING_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(api_core, "kv_get", lambda key, default=None: {})
    monkeypatch.setattr(api_core, "_now", lambda: "2026-03-14T00:00:00Z")
    monkeypatch.setattr(api_core, "_classify_activity_log_event", lambda entry: None)
    monkeypatch.setattr(live_ws, "get_open_trades", lambda: [])

    @contextmanager
    def _fake_db():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                source TEXT,
                message TEXT NOT NULL,
                data TEXT,
                created_at TEXT
            )
            """
        )
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(api_core, "get_db", _fake_db)

    app = FastAPI()
    app.include_router(websockets_router)
    client = TestClient(app)

    with client.websocket_connect("/api/ws/live") as websocket:
        init_payload = websocket.receive_json()
        assert init_payload["type"] == "init"

        ping_payload = websocket.receive_json()
        assert ping_payload["type"] == "ping"
