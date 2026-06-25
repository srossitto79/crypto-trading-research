from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from axiom.api import app
from axiom.api_core import get_pipeline_motion_log
from axiom.db import get_db


def _insert_strategy(strategy_id: str, stage: str = "backtesting") -> None:
    now = datetime.now(timezone.utc).isoformat()
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
                "ema_cross",
                "BTC",
                "1h",
                "{}",
                json.dumps({"sharpe": 1.9, "total_trades": 90, "profit_factor": 1.6}),
                stage,
                "brain",
                stage,
                now,
                now,
                now,
            ),
        )


def test_pipeline_motion_log_includes_promotion_and_demotion_with_context(AXIOM_db):
    strategy_id = "S12345"
    _insert_strategy(strategy_id)
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategy_events
            (strategy_id, from_state, to_state, actor, reason, owner_from, owner_to, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                "backtesting",
                "paper_trading",
                "brain",
                "Passed paper trading gate",
                "simulation-agent",
                "risk-manager",
                json.dumps({"total_trades": 44, "sharpe": 1.7, "profit_factor": 1.4}),
                (now - timedelta(minutes=4)).isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO strategy_events
            (strategy_id, from_state, to_state, actor, reason, owner_from, owner_to, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                "paper_trading",
                "backtesting",
                "decay_tracker",
                "Auto-demoted by decay tracker due to live Sharpe degradation",
                "risk-manager",
                "simulation-agent",
                json.dumps({"baseline_sharpe": 1.8, "live_sharpe_72h": 0.3, "degradation": 0.8333}),
                (now - timedelta(minutes=2)).isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO activity_log (level, source, message, data, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "warning",
                "decay-tracker",
                f"Decay demotion: {strategy_id} paper_trading->backtesting",
                json.dumps({"strategy_id": strategy_id, "degradation": 0.8333, "trade_count_72h": 18}),
                (now - timedelta(minutes=2)).isoformat(),
            ),
        )

    rows = get_pipeline_motion_log(limit=10)
    assert len(rows) >= 2

    promotion = next(
        row
        for row in rows
        if row.get("strategy_id") == strategy_id
        and row.get("from_state") == "gauntlet"
        and row.get("to_state") == "paper"
    )
    assert promotion["motion_type"] == "promotion"
    assert "live_trading" in promotion.get("pipelines", [])
    assert "total_trades" in (promotion.get("decision_metrics") or {})
    assert isinstance(promotion.get("layman_reason"), str)
    assert promotion.get("layman_reason")
    assert "promoted" in str(promotion.get("layman_reason")).lower()

    demotion = next(
        row
        for row in rows
        if row.get("strategy_id") == strategy_id
        and row.get("from_state") == "paper"
        and row.get("to_state") == "gauntlet"
    )
    assert demotion["motion_type"] == "demotion"
    assert demotion.get("decision_mode") == "decay_auto_demotion"
    assert isinstance(demotion.get("related_activity"), list)
    assert len(demotion.get("related_activity") or []) >= 1
    assert isinstance(demotion.get("layman_reason"), str)
    assert "demoted" in str(demotion.get("layman_reason")).lower()


def test_pipeline_motion_log_ignores_no_change_events(AXIOM_db):
    strategy_id = "S54321"
    _insert_strategy(strategy_id, stage="researching")
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategy_events
            (strategy_id, from_state, to_state, actor, reason, owner_from, owner_to, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                "researching",
                "researching",
                "brain",
                "No-op transition",
                "quant-researcher",
                "quant-researcher",
                json.dumps({"note": "noop"}),
                now,
            ),
        )

    rows = get_pipeline_motion_log(limit=20)
    assert all(
        not (
            row.get("strategy_id") == strategy_id
            and row.get("from_state") == "researching"
            and row.get("to_state") == "researching"
        )
        for row in rows
    )


def test_pipeline_motion_log_route_returns_list(AXIOM_db):
    client = TestClient(app)
    response = client.get("/api/pipeline/motion-log?limit=5")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_pipeline_motion_log_excludes_non_paper_live_motions(AXIOM_db):
    strategy_id = "S11111"
    _insert_strategy(strategy_id, stage="developing")
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategy_events
            (strategy_id, from_state, to_state, actor, reason, owner_from, owner_to, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                "developing",
                "backtesting",
                "brain",
                "progression",
                "strategy-developer",
                "simulation-agent",
                json.dumps({"note": "internal pipeline transition"}),
                now,
            ),
        )

    rows = get_pipeline_motion_log(limit=50)
    assert all(
        not (
            row.get("strategy_id") == strategy_id
            and row.get("from_state") == "quick_screen"
            and row.get("to_state") == "gauntlet"
        )
        for row in rows
    )
