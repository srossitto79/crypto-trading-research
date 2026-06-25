import asyncio
from datetime import datetime, timedelta, timezone

from axiom.bot import (
    AxiomBot,
    _stale_recovery_interval_seconds,
    _stale_recovery_minutes,
    should_queue_bootstrap_brain_cycle,
)
from axiom.task_timeouts import recommended_stale_recovery_minutes


def test_stale_recovery_interval_has_minimum_floor():
    assert _stale_recovery_interval_seconds(1) == 300.0
    assert _stale_recovery_interval_seconds(5) == 300.0
    assert _stale_recovery_interval_seconds(10) == 600.0


def test_stale_recovery_minutes_clamps_below_safe_runtime(monkeypatch):
    monkeypatch.setattr(
        "axiom.db.kv_get",
        lambda key, default=None: {"task_stale_recovery_minutes": 7} if key == "axiom:settings" else default,
    )
    monkeypatch.setattr("axiom.bot.load_config", lambda: {})

    assert _stale_recovery_minutes() == recommended_stale_recovery_minutes({})


def test_maybe_recover_stale_tasks_skips_when_inflight(monkeypatch):
    bot = AxiomBot(agent_id=None)
    calls: list[dict] = []

    def fake_recover_stale_running_tasks(stale_minutes: int = 10, **kwargs):
        calls.append({"stale_minutes": int(stale_minutes), "kwargs": kwargs})
        return {"agent_requeued": 0, "agent_failed": 0, "brain_requeued": 0}

    monkeypatch.setattr("axiom.bot._stale_recovery_minutes", lambda: 10)
    monkeypatch.setattr("axiom.db.recover_stale_running_tasks", fake_recover_stale_running_tasks)

    bot._last_stale_recovery_at = 0.0
    bot._active_agent_task_ids.add(123)
    asyncio.run(bot._maybe_recover_stale_tasks("agent"))

    assert calls == []


def test_maybe_recover_stale_tasks_throttles(monkeypatch):
    bot = AxiomBot(agent_id=None)
    calls: list[dict] = []
    clock = {"now": 1000.0}

    def fake_recover_stale_running_tasks(stale_minutes: int = 10, **kwargs):
        calls.append({"stale_minutes": int(stale_minutes), "kwargs": kwargs})
        return {"agent_requeued": 1, "agent_failed": 0, "brain_requeued": 0}

    monkeypatch.setattr("axiom.bot._stale_recovery_minutes", lambda: 1)
    monkeypatch.setattr("axiom.bot.time.time", lambda: clock["now"])
    monkeypatch.setattr("axiom.db.recover_stale_running_tasks", fake_recover_stale_running_tasks)
    monkeypatch.setattr("axiom.db.STALE_RECOVERY_FAIL_AGENTS", ("execution-trader",))

    bot._last_stale_recovery_at = 0.0
    asyncio.run(bot._maybe_recover_stale_tasks("agent"))
    assert calls == [{"stale_minutes": 1, "kwargs": {"fail_agents": ("execution-trader",)}}]

    clock["now"] = 1100.0  # < 300s minimum interval
    asyncio.run(bot._maybe_recover_stale_tasks("brain"))
    assert calls == [{"stale_minutes": 1, "kwargs": {"fail_agents": ("execution-trader",)}}]

    clock["now"] = 1301.0  # > 300s minimum interval
    asyncio.run(bot._maybe_recover_stale_tasks("brain"))
    assert calls == [
        {"stale_minutes": 1, "kwargs": {"fail_agents": ("execution-trader",)}},
        {"stale_minutes": 1, "kwargs": {"fail_agents": ("execution-trader",)}},
    ]


def test_should_queue_bootstrap_brain_cycle_skips_recent_bootstrap_tasks(AXIOM_db):
    from axiom.db import get_db

    now = datetime(2026, 4, 14, 19, 10, 0, tzinfo=timezone.utc)
    fresh_created = (now - timedelta(seconds=30)).isoformat()
    stale_created = (now - timedelta(minutes=10)).isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (type, payload, status, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            ("brain_invoke", '{"source":"bootstrap","message":"fresh"}', "running", 1, fresh_created),
        )
        conn.execute(
            "INSERT INTO tasks (type, payload, status, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            ("brain_invoke", '{"source":"bootstrap","message":"stale"}', "done", 1, stale_created),
        )

    assert should_queue_bootstrap_brain_cycle(now=now) is False


def test_should_queue_bootstrap_brain_cycle_allows_new_task_after_cooldown(AXIOM_db):
    from axiom.db import get_db

    now = datetime(2026, 4, 14, 19, 10, 0, tzinfo=timezone.utc)
    stale_created = (now - timedelta(minutes=10)).isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (type, payload, status, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            ("brain_invoke", '{"source":"bootstrap","message":"stale"}', "done", 1, stale_created),
        )

    assert should_queue_bootstrap_brain_cycle(now=now) is True


def test_bot_pid_probe_tolerates_windows_access_denied(monkeypatch):
    import sys

    class _Kernel32:
        def OpenProcess(self, *_args):
            return 0

        def CloseHandle(self, _handle):
            return 1

    class _Ctypes:
        windll = type("windll", (), {"kernel32": _Kernel32()})()

        @staticmethod
        def GetLastError():
            return 5

    monkeypatch.setattr("axiom.bot.os.name", "nt")
    monkeypatch.setitem(sys.modules, "ctypes", _Ctypes)

    from axiom import bot as bot_mod

    assert bot_mod._is_pid_running(12345) is True


def test_bot_process_pending_tasks_bootstrap_dispatches_strategy_developer_research_without_llm(AXIOM_db, monkeypatch):
    from axiom.db import get_db

    bot = AxiomBot(agent_id=None)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, type, payload, status, priority, created_at)
            VALUES (?, 'brain_invoke', ?, 'running', 1, ?)
            """,
            (
                501,
                '{"source":"bootstrap","message":"axiom just started."}',
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    delegated: list[str] = []

    async def _noop_recovery(*_args, **_kwargs):
        return None

    async def _unexpected_call(*args, **kwargs):
        raise AssertionError("bootstrap should not invoke the Brain LLM")

    monkeypatch.setattr(bot, "_maybe_recover_stale_tasks", _noop_recovery)
    monkeypatch.setattr("axiom.db.claim_pending_tasks", lambda *args, **kwargs: [{"id": 501, "payload": '{"source":"bootstrap","message":"axiom just started."}'}])
    monkeypatch.setattr("axiom.brain.assign_research_cycle", lambda: delegated.append("research"))
    monkeypatch.setattr("axiom.agents.runner._call_with_tools", _unexpected_call)

    asyncio.run(bot._process_pending_tasks())

    assert delegated == ["research"]

    with get_db() as conn:
        row = conn.execute("SELECT status, completed_at, result FROM tasks WHERE id = 501").fetchone()

    assert row["status"] == "done"
    assert row["completed_at"]
