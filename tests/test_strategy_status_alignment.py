"""Tests for strategy status normalization and scanner-active loading."""

from datetime import datetime, timezone

from axiom.db import get_db, init_db
from axiom.scanner import _load_deployed_strategies


def _insert_strategy(
    strategy_id: str,
    status: str,
    stage: str,
    strategy_type: str = "rsi_momentum",
):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, status, stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                strategy_id,
                strategy_type,
                "BTC",
                "1h",
                "{}",
                status,
                stage,
                now,
                now,
            ),
        )


def test_scanner_loads_active_statuses(AXIOM_db):
    # Use canonical S-prefixed IDs so ID normalization migration doesn't rename them.
    # The scanner deploys on the `stage` column (paper%/live%/deploy%), so set it
    # explicitly — status alone is not the deployment signal.
    _insert_strategy("S00090", "paper", stage="paper")
    _insert_strategy("S00091", "deployed", stage="deployed")
    _insert_strategy("S00092", "retired", stage="archived")

    # Run init_db to trigger status/stage normalization migrations.
    init_db()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, status FROM strategies WHERE id IN (?, ?, ?)",
            ("S00090", "S00091", "S00092"),
        ).fetchall()

    status_by_id = {row["id"]: row["status"] for row in rows}
    assert status_by_id["S00090"] == "paper"
    assert status_by_id["S00091"] == "live_graduated"

    loaded = _load_deployed_strategies()
    assert "S00090" in loaded
    assert "S00091" in loaded
    assert "S00092" not in loaded
