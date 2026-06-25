from __future__ import annotations

from datetime import datetime, timedelta, timezone

from axiom.db import kv_set
from axiom.runtime_health import normalize_daemon_state


def test_normalize_daemon_state_marks_stale_dead_process(monkeypatch, AXIOM_db):
    stale_tick = datetime.now(timezone.utc) - timedelta(minutes=30)
    kv_set(
        "daemon_state",
        {
            "running": True,
            "pid": 43210,
            "last_scan": stale_tick.isoformat(),
            "last_tick_ts": stale_tick.timestamp(),
        },
    )

    removed = {"called": False}
    monkeypatch.setattr("axiom.runtime_health.pid_exists", lambda pid: False)
    monkeypatch.setattr(
        "axiom.runtime_health.remove_stale_daemon_lock",
        lambda expected_pid=None: removed.__setitem__("called", True) or True,
    )

    state = normalize_daemon_state(stale_after_seconds=60, write_back=False)

    assert state["running"] is False
    assert state["stale_process_detected"] is True
    assert state["stale_pid"] == 43210
    assert state["process_alive"] is False
    assert removed["called"] is True
