from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from axiom.db import get_db
from axiom.lab_strategy_pool import list_strategy_pool_candidates


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_strategy(
    *,
    strategy_id: str,
    name: str,
    strategy_type: str,
    stage: str,
    status: str | None = None,
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    params: dict | None = None,
) -> None:
    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (
                id, name, type, runtime_type, symbol, timeframe, params, metrics,
                status, stage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                name,
                strategy_type,
                strategy_type,
                symbol,
                timeframe,
                json.dumps(params or {}),
                json.dumps({}),
                status or stage,
                stage,
                now,
                now,
            ),
        )


def _insert_archived_strategy(*, strategy_id: str, name: str, strategy_type: str) -> None:
    payload = {
        "id": strategy_id,
        "name": name,
        "type": strategy_type,
        "runtime_type": strategy_type,
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "params": {},
        "metrics": {},
        "status": "archived",
        "stage": "archived",
    }
    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO archived_strategies (id, original_data, archived_at, archived_by, reason) VALUES (?, ?, ?, ?, ?)",
            (
                strategy_id,
                json.dumps(payload),
                now,
                "test",
                "graveyard candidate",
            ),
        )


def test_strategy_pool_reads_active_and_graveyard_candidates(AXIOM_db):
    _insert_strategy(strategy_id="S_ACTIVE", name="Paper Winner", strategy_type="ADX_TREND", stage="paper")
    _insert_strategy(strategy_id="S_LIVE", name="Live Winner", strategy_type="williams_r", stage="live_graduated")
    _insert_strategy(strategy_id="S_BACK", name="Gauntlet", strategy_type="williams_r", stage="gauntlet")
    _insert_strategy(strategy_id="S_REJECT", name="Rejected", strategy_type="ADX_TREND", stage="rejected")
    _insert_archived_strategy(strategy_id="S_ARCH", name="Archived Alpha", strategy_type="williams_r")

    active_and_graveyard = list_strategy_pool_candidates(
        strategy_sources=["active", "graveyard"],
    )
    ids = {row["strategy_id"] for row in active_and_graveyard}

    assert {"S_ACTIVE", "S_LIVE", "S_REJECT", "S_ARCH"}.issubset(ids)
    assert "S_BACK" not in ids

    by_id = {row["strategy_id"]: row for row in active_and_graveyard}
    assert by_id["S_ACTIVE"]["source_pool"] == "active"
    assert by_id["S_LIVE"]["source_pool"] == "active"
    assert by_id["S_REJECT"]["source_pool"] == "graveyard"
    assert by_id["S_ARCH"]["source_pool"] == "graveyard"


def test_strategy_pool_all_managed_includes_backtesting(AXIOM_db):
    _insert_strategy(strategy_id="S_PAPER", name="Paper", strategy_type="ADX_TREND", stage="paper")
    _insert_strategy(strategy_id="S_BACK", name="Backtesting", strategy_type="williams_r", stage="gauntlet")

    rows = list_strategy_pool_candidates(
        strategy_sources=["all_managed"],
    )
    ids = {row["strategy_id"] for row in rows}

    assert "S_PAPER" in ids
    assert "S_BACK" in ids


def test_strategy_pool_skips_local_backtest_incompatible_candidates(AXIOM_db):
    _insert_strategy(
        strategy_id="S_BAD",
        name="Unsupported",
        strategy_type="ema_cross",
        stage="paper",
        params={"stop_loss_pct": 2.0},
    )
    _insert_strategy(strategy_id="S_GOOD", name="Supported", strategy_type="ADX_TREND", stage="paper")

    rows = list_strategy_pool_candidates(strategy_sources=["active"])
    ids = {row["strategy_id"] for row in rows}

    assert "S_GOOD" in ids
    assert "S_BAD" not in ids


def test_lab_strategy_pool_module_keeps_production_reads_read_only():
    source = Path("Axiom/lab_strategy_pool.py").read_text(encoding="utf-8")
    assert "from axiom.db import get_db" not in source
    assert "axiom.db.get_db" not in source
    assert "mode=ro" in source
