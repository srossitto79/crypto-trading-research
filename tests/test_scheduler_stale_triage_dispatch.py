"""End-to-end test for the Axiom-stale-triage scheduler dispatch handler.

Covers the wiring from `run_job` → payload parsing → `if kind == "stale_triage"`
branch → actual transition_stage calls. The CLI-level algorithm is covered by
tests/test_cli_triage_stale.py; this test exercises the scheduler code path.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json


def _seed(conn, sid: str, stage: str, stage_changed_offset_days: int) -> None:
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stage_changed_offset_days)).isoformat()
    conn.execute(
        """
        INSERT INTO strategies (id, name, stage, status, type, symbol, timeframe, created_at, stage_changed_at)
        VALUES (?, ?, ?, ?, 'momentum', 'BTCUSDT', '1h', ?, ?)
        """,
        (sid, f"name-{sid}", stage, stage, ts, ts),
    )


def test_scheduler_stale_triage_dispatch_archives_stale(recwarn):
    """Invoking run_job with kind=stale_triage must archive matching strategies."""
    from axiom.db import get_db, init_db
    from axiom.scheduler import run_job

    init_db()

    with get_db() as conn:
        _seed(conn, "SCH-OLD", "quick_screen", stage_changed_offset_days=10)
        _seed(conn, "SCH-NEW", "quick_screen", stage_changed_offset_days=1)
        _seed(conn, "SCH-GAUNTLET", "gauntlet", stage_changed_offset_days=30)

    job = {
        "id": "Axiom-stale-triage",
        "name": "Stale Quick-Screen Triage",
        "command": "stale-triage",
        "payload": json.dumps({"kind": "stale_triage", "days": 7}),
    }

    status, error = asyncio.run(run_job(job))
    assert status == "ok", f"expected ok, got status={status} error={error}"
    assert error is None
    assert not [warning for warning in recwarn if issubclass(warning.category, RuntimeWarning)]

    with get_db() as conn:
        stages = {
            row["id"]: row["stage"]
            for row in conn.execute("SELECT id, stage FROM strategies")
        }
    assert stages["SCH-OLD"] == "archived"
    assert stages["SCH-NEW"] == "quick_screen"
    assert stages["SCH-GAUNTLET"] == "gauntlet"
