"""Tests for `Axiom strategies triage-stale` — bulk-archives stale quick_screen strategies."""
from __future__ import annotations

import datetime as dt
from click.testing import CliRunner

from axiom.cli import cli
from axiom.db import get_db, init_db


def _seed(conn, sid: str, stage: str, stage_changed_offset_days: int) -> None:
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stage_changed_offset_days)).isoformat()
    conn.execute(
        """
        INSERT INTO strategies (id, name, stage, status, type, symbol, timeframe, created_at, stage_changed_at)
        VALUES (?, ?, ?, ?, 'momentum', 'BTCUSDT', '1h', ?, ?)
        """,
        (sid, f"name-{sid}", stage, stage, ts, ts),
    )


def _seed_recent_task(conn, sid: str, days_ago: float) -> None:
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()
    conn.execute(
        """
        INSERT INTO agent_tasks (agent_id, type, strategy_id, status, created_at)
        VALUES ('a', 'backtest', ?, 'completed', ?)
        """,
        (sid, ts),
    )


def test_triage_stale_dry_run_no_changes():
    init_db()

    with get_db() as conn:
        _seed(conn, "S-OLD", "quick_screen", stage_changed_offset_days=10)
        _seed(conn, "S-NEW", "quick_screen", stage_changed_offset_days=1)

    runner = CliRunner()
    result = runner.invoke(cli, ["strategies", "triage-stale", "--days", "7"])
    assert result.exit_code == 0, result.output
    assert "S-OLD" in result.output
    assert "dry-run" in result.output.lower()

    with get_db() as conn:
        stages = {
            row["id"]: row["stage"]
            for row in conn.execute("SELECT id, stage FROM strategies")
        }
    assert stages["S-OLD"] == "quick_screen"  # unchanged
    assert stages["S-NEW"] == "quick_screen"


def test_triage_stale_apply_archives_stale():
    init_db()

    with get_db() as conn:
        _seed(conn, "S-OLD-NOACT", "quick_screen", stage_changed_offset_days=10)
        _seed(conn, "S-OLD-ACTIVE", "quick_screen", stage_changed_offset_days=10)
        _seed_recent_task(conn, "S-OLD-ACTIVE", days_ago=1)
        _seed(conn, "S-NEW", "quick_screen", stage_changed_offset_days=1)
        _seed(conn, "S-GAUNTLET", "gauntlet", stage_changed_offset_days=30)

    runner = CliRunner()
    result = runner.invoke(cli, ["strategies", "triage-stale", "--days", "7", "--apply"])
    assert result.exit_code == 0, result.output

    with get_db() as conn:
        stages = {
            row["id"]: row["stage"]
            for row in conn.execute("SELECT id, stage FROM strategies")
        }
    assert stages["S-OLD-NOACT"] == "archived"
    assert stages["S-OLD-ACTIVE"] == "quick_screen"  # recent task protects it
    assert stages["S-NEW"] == "quick_screen"
    assert stages["S-GAUNTLET"] == "gauntlet"  # only quick_screen is triaged
