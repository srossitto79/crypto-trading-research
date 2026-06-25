from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from axiom.api_domains import paper as paper_domain
from axiom.brain import transition_stage
from axiom.db import get_db
from axiom.sim import data_pump


def _insert_strategy(
    strategy_id: str,
    *,
    stage: str = "paper",
    created_at: str | None = None,
) -> str:
    now = created_at or datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, stage_changed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                strategy_id,
                "rsi_momentum",
                "BTC/USDT",
                "1h",
                "{}",
                "{}",
                stage,
                "brain",
                stage,
                now,
                now,
                now,
            ),
        )
    return now


def test_collect_compat_paper_sessions_ignores_trades_from_prior_strategy_incarnation(AXIOM_db, monkeypatch):
    created_at = _insert_strategy("S-PAPER-NEW", stage="paper")
    created_ts = datetime.fromisoformat(created_at)
    old_trade_open = (created_ts - timedelta(days=2)).isoformat()
    new_trade_open = (created_ts + timedelta(hours=1)).isoformat()
    new_trade_close = (created_ts + timedelta(hours=2)).isoformat()

    monkeypatch.setattr(
        paper_domain.trading_domain,
        "read_recent_trades",
        lambda limit=5000: [
            {
                "id": "T-OLD",
                "strategy_id": "S-PAPER-NEW",
                "strategy": "S-PAPER-NEW",
                "asset": "BTC",
                "direction": "long",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "size": 1.0,
                "pnl": 1.0,
                "pnl_pct": 0.01,
                "status": "CLOSED",
                "opened_at": old_trade_open,
                "closed_at": old_trade_open,
                "signal_data": "{}",
            },
            {
                "id": "T-NEW",
                "strategy_id": "S-PAPER-NEW",
                "strategy": "S-PAPER-NEW",
                "asset": "BTC",
                "direction": "long",
                "entry_price": 100.0,
                "exit_price": 103.0,
                "size": 1.0,
                "pnl": 3.0,
                "pnl_pct": 0.03,
                "status": "CLOSED",
                "opened_at": new_trade_open,
                "closed_at": new_trade_close,
                "signal_data": "{}",
            },
        ],
    )
    monkeypatch.setattr(
        paper_domain,
        "kv_get",
        lambda key, default=None: {} if key in {"daemon_state", "scanner_state"} else default,
    )

    sessions = paper_domain._collect_compat_paper_sessions()

    assert len(sessions) == 1
    assert sessions[0]["total_trades"] == 1
    assert sessions[0]["id"].startswith("compat:strategy:S-PAPER-NEW:")


def test_collect_compat_paper_sessions_surfaces_hedged_positions(AXIOM_db, monkeypatch):
    created_at = _insert_strategy("S-PAPER-HEDGE", stage="paper")
    created_ts = datetime.fromisoformat(created_at)
    open_time = (created_ts + timedelta(hours=1)).isoformat()

    monkeypatch.setattr(
        paper_domain.trading_domain,
        "read_recent_trades",
        lambda limit=5000: [
            {
                "id": "T-LONG",
                "strategy_id": "S-PAPER-HEDGE",
                "strategy": "S-PAPER-HEDGE",
                "asset": "BTC",
                "direction": "long",
                "entry_price": 100.0,
                "size": 1.0,
                "pnl": None,
                "pnl_pct": None,
                "status": "OPEN",
                "opened_at": open_time,
                "closed_at": None,
                "signal_data": "{}",
            },
            {
                "id": "T-SHORT",
                "strategy_id": "S-PAPER-HEDGE",
                "strategy": "S-PAPER-HEDGE",
                "asset": "BTC",
                "direction": "short",
                "entry_price": 101.0,
                "size": 0.5,
                "pnl": None,
                "pnl_pct": None,
                "status": "OPEN",
                "opened_at": open_time,
                "closed_at": None,
                "signal_data": "{}",
            },
        ],
    )
    monkeypatch.setattr(
        paper_domain,
        "kv_get",
        lambda key, default=None: {} if key in {"daemon_state", "scanner_state"} else default,
    )

    sessions = paper_domain._collect_compat_paper_sessions()

    assert len(sessions) == 1
    session = sessions[0]
    assert session["position"] is None
    assert len(session["positions"]) == 2
    assert session["trade_mode"] == "both"
    assert session["position_model"] == "hedged"
    assert session["net_position"] is not None
    assert session["net_position"]["position_count"] == 2


def test_get_cached_candles_prefers_numeric_timestamp_column(tmp_path, monkeypatch):
    db_path = tmp_path / "sim_candles.db"
    monkeypatch.setattr(data_pump, "_DB_PATH", str(db_path))

    table_name = data_pump._get_table_name("BTC", "1h")
    rows = pd.DataFrame(
        {
            "t": [
                "2026-03-10T00:00:00+00:00",
                "2026-03-10T01:00:00+00:00",
            ],
            "open": [100.0, 101.0],
            "high": [100.5, 101.5],
            "low": [99.5, 100.5],
            "close": [100.2, 101.2],
            "volume": [1_000.0, 1_100.0],
            "t_ms": [1_762_563_600_000, 1_762_567_200_000],
        }
    )

    with sqlite3.connect(db_path) as conn:
        rows.to_sql(table_name, conn, if_exists="replace", index=False)

    frame = data_pump.get_cached_candles("BTC", "1h", 1_762_565_000_000, 10)

    assert frame is not None
    assert len(frame) == 1
    assert float(frame.iloc[0]["close"]) == 100.2


def test_force_transition_records_audit_and_warning_activity(AXIOM_db, monkeypatch):
    _insert_strategy("S-FORCE-AUDIT", stage="rejected")
    activity_events: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "axiom.brain.log_activity",
        lambda level, source, message, *args, **kwargs: activity_events.append((level, source, message)),
    )

    result = transition_stage(
        strategy_id="S-FORCE-AUDIT",
        target_stage="paper",
        reason="manual recovery for testing",
        actor="manual",
        force=True,
    )

    with get_db() as conn:
        row = conn.execute(
            "SELECT audit_summary FROM strategies WHERE id = ?",
            ("S-FORCE-AUDIT",),
        ).fetchone()
        event = conn.execute(
            "SELECT details_json FROM strategy_events WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
            ("S-FORCE-AUDIT",),
        ).fetchone()

    audit_summary = json.loads(row["audit_summary"] or "[]")
    event_details = json.loads(event["details_json"] or "{}")

    assert result["to"] == "paper"
    assert audit_summary[-1]["force"] is True
    assert event_details["force"] is True
    assert any(level == "warning" and source == "brain" for level, source, _message in activity_events)
