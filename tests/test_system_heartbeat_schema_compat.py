"""Regression tests for heartbeat/paper-session schema compatibility."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from axiom.api_domains import paper as paper_domain
from axiom.api_domains import trading as trading_domain


def test_collect_compat_sessions_handles_missing_compatible_regimes_column(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            CREATE TABLE strategies (
                id TEXT PRIMARY KEY,
                display_id TEXT,
                name TEXT NOT NULL,
                type TEXT,
                symbol TEXT,
                timeframe TEXT,
                params TEXT,
                stage TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                metrics TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategies
                (id, display_id, name, type, symbol, timeframe, params, stage, status, created_at, updated_at, metrics)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "S00001",
                "P00001",
                "Compat Session",
                "ema_cross",
                "BTC/USDT",
                "1h",
                "{}",
                "paper_trading",
                "paper",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T01:00:00+00:00",
                "{}",
            ),
        )
        conn.commit()

        @contextmanager
        def _fake_get_db():
            yield conn

        def _fake_kv_get(key: str, default=None):
            if key in {"daemon_state", "scanner_state"}:
                return {}
            return default

        monkeypatch.setattr(paper_domain, "get_db", _fake_get_db)
        monkeypatch.setattr(paper_domain, "kv_get", _fake_kv_get)
        monkeypatch.setattr(trading_domain, "read_recent_trades", lambda limit=5000: [])

        sessions = paper_domain._collect_compat_paper_sessions()
    finally:
        conn.close()

    assert len(sessions) == 1
    assert sessions[0]["strategy_name"] == "P00001"
    assert sessions[0]["status"] == "watching"
