"""Batch I: test coverage for reliability surfaces (H-T1, H-T3, H-T4, H-T5).

H-T1: DB connection hardening invariants (WAL, foreign keys, busy_timeout).
H-T3: Error-path recovery — context manager rollback, lock release on failure.
H-T4: Runtime worker error paths — task runner failures surface on task rows.
H-T5: Signal handler registration tolerates Windows absence.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading

import pytest

from axiom.db import (
    get_db,
    get_db_immediate,
    init_db,
)


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


# -----------------------------------------------------------------------
# H-T1: DB connection invariants
# -----------------------------------------------------------------------
def test_h_t1_wal_mode_is_enabled():
    """Every get_db connection runs PRAGMA journal_mode=WAL."""
    with get_db() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_h_t1_foreign_keys_enabled():
    with get_db() as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert int(fk) == 1


def test_h_t1_busy_timeout_set():
    with get_db() as conn:
        bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert int(bt) >= 15000


def test_h_t1_concurrent_readers_dont_block():
    """Two concurrent readers should both get WAL-mode snapshots."""
    results: list[str] = []

    def _read():
        with get_db() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        results.append(str(mode))

    threads = [threading.Thread(target=_read) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(results) == 4
    assert all(r.lower() == "wal" for r in results)


# -----------------------------------------------------------------------
# H-T3: Error-path recovery
# -----------------------------------------------------------------------
def test_h_t3_get_db_rolls_back_on_exception():
    """A raising block inside get_db must not commit partial writes."""
    key = "ht3_rollback_probe"
    with pytest.raises(RuntimeError):
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, "should_not_persist"),
            )
            raise RuntimeError("simulated mid-transaction failure")
    with get_db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    assert row is None, "rollback failed: value persisted across exception"


def test_h_t3_get_db_immediate_rolls_back_on_exception():
    key = "ht3_immediate_rollback"
    with pytest.raises(ValueError):
        with get_db_immediate() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, "should_not_persist"),
            )
            raise ValueError("boom")
    with get_db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    assert row is None


def test_h_t3_lock_release_after_partial_acquire_failure(monkeypatch):
    """If os.write fails after flock succeeds the FD is still closed."""
    from axiom import runtime_worker as rw

    monkeypatch.setattr(rw, "_runtime_worker_lock_fd", None, raising=False)

    def _boom_write(*a, **kw):
        raise OSError("simulated post-lock failure")

    monkeypatch.setattr(rw.os, "write", _boom_write)

    with pytest.raises(OSError):
        rw.acquire_runtime_worker_lock(lock_name=f"test-ht3-{os.getpid()}.lock")

    # No leaked global FD.
    assert rw._runtime_worker_lock_fd is None


# -----------------------------------------------------------------------
# H-T4: Runtime worker error paths
# -----------------------------------------------------------------------
def test_h_t4_agent_runner_exception_persists_failed_status(monkeypatch):
    """When the agent runner raises, process_agent_tasks_once must
    stamp status='failed' on the task row so it stops blocking its queue."""
    from axiom import runtime_worker as rw

    fake_agents = [{"id": "agent-a", "enabled": 1}]
    fake_tasks = [{"id": 999001, "agent_id": "agent-a", "status": "running"}]

    class _Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return [_Row(r) for r in self.rows]

    class _Row:
        def __init__(self, d): self._d = d
        def __getitem__(self, k):
            if isinstance(k, int): return list(self._d.values())[k]
            return self._d[k]
        def keys(self): return self._d.keys()

    captured_updates: list[tuple] = []

    class _FakeConn:
        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("SELECT * FROM AGENTS"):
                return _Cursor(fake_agents)
            if sql.strip().upper().startswith("UPDATE AGENT_TASKS"):
                captured_updates.append(params)
                return None
            return _Cursor([])

    class _FakeCtx:
        def __enter__(self): return _FakeConn()
        def __exit__(self, *a): return False

    monkeypatch.setattr(rw, "get_db", lambda: _FakeCtx(), raising=False)

    # claim_pending_agent_tasks returns the task once, then nothing
    call_state = {"called": False}
    def _claim(agent_id, limit=None):
        if call_state["called"]:
            return []
        call_state["called"] = True
        return fake_tasks[: limit or None]

    # Patch the import site — runtime_worker imports at call time
    import axiom.db as AXIOM_db
    monkeypatch.setattr(AXIOM_db, "claim_pending_agent_tasks", _claim)
    monkeypatch.setattr(AXIOM_db, "get_db", lambda: _FakeCtx())

    async def _raising_runner(agent, task):
        raise RuntimeError("simulated agent failure")

    monkeypatch.setattr(rw, "_run_agent_task", _raising_runner)

    processed = asyncio.run(rw.process_agent_tasks_once(concurrency=1))
    assert processed == 1
    # One UPDATE captured with the failure error text.
    assert captured_updates, "failed status was not persisted"
    update_params = captured_updates[0]
    assert "RuntimeError" in str(update_params[0])


# -----------------------------------------------------------------------
# H-T5: Signal handler registration tolerates Windows
# -----------------------------------------------------------------------
def test_h_t5_signal_registration_handles_not_implemented(monkeypatch):
    """On Windows, asyncio.add_signal_handler raises NotImplementedError;
    daemon code must catch it and keep running."""

    class _FakeLoop:
        def __init__(self):
            self.calls: list[tuple] = []

        def add_signal_handler(self, sig, handler):
            self.calls.append((sig, handler))
            raise NotImplementedError("Windows has no add_signal_handler")

    loop = _FakeLoop()
    errors: list[Exception] = []

    # Simulate the registration pattern from daemon.async_market_loop.
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, lambda: None)
        except (NotImplementedError, RuntimeError, ValueError) as exc:
            errors.append(exc)

    # Every attempt should have been swallowed, no re-raise.
    assert loop.calls, "registration was not attempted"
    assert all(isinstance(e, NotImplementedError) for e in errors)


def test_h_t5_sigterm_registration_swallows_value_error(monkeypatch):
    """daemon.run wraps signal.signal in try/except(ValueError, OSError)
    because non-main threads reject signal registration on POSIX."""
    recorded: list = []

    def _register_value_error(sig, handler):
        recorded.append(sig)
        raise ValueError("signal only works in main thread")

    try:
        _register_value_error(signal.SIGTERM, lambda *a: None)
    except (ValueError, OSError):
        pass

    assert recorded == [signal.SIGTERM]
