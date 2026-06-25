"""Live-position indicator / signal / marker endpoints.

These mirror the paper-trading panels for live/deployed strategies. The indicator
and marker handlers are thin delegations to the paper domain (paper "sessions" are
a compat facade over the strategies table and resolve a bare strategy_id), and the
signals handler builds pending ('approaching') signals from the last scan's snapshot
via the same paper-domain helpers. Tests pin the delegation + signal shape without
hitting the network (candle fetches are mocked / not exercised).
"""

import pytest
from fastapi import HTTPException

import axiom.api_domains.trading as t
from axiom.db import get_db, kv_set


def _seed_strategy(sid: str, stage: str = "deployed", timeframe: str = "4h") -> str:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, status, stage, owner, display_id, symbol, timeframe, created_at) "
            "VALUES (?, ?, 'rsi_momentum', ?, ?, 'brain', ?, 'BTC/USDT', ?, ?)",
            (sid, sid, stage, stage, sid, timeframe, t._now()),
        )
        conn.commit()
    return sid


def test_live_indicators_delegates_with_resolved_timeframe(AXIOM_db, monkeypatch):
    _seed_strategy("S-IND", timeframe="4h")
    captured: dict = {}

    def _fake(session_id, indicators=None, limit=500, timeframe=None):
        captured.update(session_id=session_id, limit=limit, timeframe=timeframe)
        return {"session_id": session_id, "config": {}, "indicators": {}}

    monkeypatch.setattr("axiom.api_domains.paper.get_paper_session_indicators", _fake)
    out = t.read_live_indicators("S-IND")  # no explicit tf -> resolve from strategy row
    assert captured["session_id"] == "S-IND"
    assert captured["timeframe"] == "4h"  # resolved from the strategy row
    assert out["config"] == {}


def test_live_indicators_explicit_timeframe_wins(AXIOM_db, monkeypatch):
    _seed_strategy("S-IND2", timeframe="4h")
    captured: dict = {}
    monkeypatch.setattr(
        "axiom.api_domains.paper.get_paper_session_indicators",
        lambda session_id, indicators=None, limit=500, timeframe=None: captured.update(timeframe=timeframe) or {},
    )
    t.read_live_indicators("S-IND2", timeframe="1h")
    assert captured["timeframe"] == "1h"


def test_live_markers_delegates(AXIOM_db, monkeypatch):
    _seed_strategy("S-MK")
    captured: dict = {}

    def _fake(session_id, limit=500, include_generated=False):
        captured.update(session_id=session_id, limit=limit, include_generated=include_generated)
        return {"entries": [], "exits": [], "blocked": []}

    monkeypatch.setattr("axiom.api_domains.paper.get_paper_session_markers", _fake)
    out = t.read_live_markers("S-MK", limit=100)
    assert captured == {"session_id": "S-MK", "limit": 100, "include_generated": False}
    assert out["entries"] == []


def test_live_signals_builds_pending_from_scanner_snapshot(AXIOM_db):
    _seed_strategy("S-SIG")
    kv_set(
        "scanner_state",
        {
            "last_scan": "2026-06-17T00:00:00+00:00",
            "signals": {
                "s-sig": {"rsi": 28.0, "adx": 30.0, "entry_signal": True, "exit_signal": False},
            },
        },
    )
    out = t.read_live_signals("S-SIG")
    assert out["strategy_id"] == "S-SIG"
    assert out["last_signal"] == "entry"
    assert out["last_scan"] == "2026-06-17T00:00:00+00:00"
    assert any(s["signal_type"] == "entry" for s in out["pending_signals"])
    assert "rsi" in out["indicators"] and out["indicators"]["rsi"]["value"] == 28.0


def test_live_signals_no_snapshot_is_empty(AXIOM_db):
    _seed_strategy("S-NONE")
    kv_set("scanner_state", {"last_scan": "2026-06-17T00:00:00+00:00", "signals": {}})
    out = t.read_live_signals("S-NONE")
    assert out["pending_signals"] == []
    assert out["last_signal"] == "none"


def test_empty_strategy_id_rejected(AXIOM_db):
    for fn in (t.read_live_indicators, t.read_live_markers, t.read_live_signals):
        with pytest.raises(HTTPException):
            fn("")
