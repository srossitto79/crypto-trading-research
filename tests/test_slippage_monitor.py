from datetime import datetime, timedelta, timezone

from axiom.db import get_db
from axiom.monitoring import run_slippage_monitor


def test_run_slippage_monitor_inserts_audit_rows_and_updates_trade(AXIOM_db):
    now = datetime.now(timezone.utc)
    opened_at = (now - timedelta(hours=2)).isoformat()
    closed_at = (now - timedelta(hours=1)).isoformat()

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO trades (
                id, strategy, strategy_id, asset, direction,
                signal_entry_price, fill_entry_price,
                status, opened_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "E-SLIP-1",
                "S-SLIP",
                "S-SLIP",
                "BTC",
                "long",
                100.0,
                100.5,
                "CLOSED",
                opened_at,
                closed_at,
            ),
        )

    summary = run_slippage_monitor(lookback_hours=24, max_trades=10)

    assert summary["candidate_samples"] == 1
    assert summary["changed_samples"] == 1
    assert summary["strategies_with_penalties"] == 1

    with get_db() as conn:
        audit_row = conn.execute(
            "SELECT trade_id, leg, source, analyzed_at FROM trade_slippage_audit WHERE trade_id = ?",
            ("E-SLIP-1",),
        ).fetchone()
        trade_row = conn.execute(
            "SELECT entry_slippage_bps FROM trades WHERE id = ?",
            ("E-SLIP-1",),
        ).fetchone()

    assert audit_row is not None
    assert audit_row["leg"] == "entry"
    assert audit_row["source"] == "slippage_monitor"
    assert audit_row["analyzed_at"] is not None
    assert trade_row["entry_slippage_bps"] is not None
