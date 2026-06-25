"""Regression tests for the daemon-loop hot-restart log-spam defect.

When a standalone ``Axiom daemon start`` process already owns the singleton
daemon lock, the API-hosted ``run_in_loop()`` intentionally declines to start a
second data/risk loop. Previously it returned a bare ``None``, which the generic
``_supervise_background_loop`` could not distinguish from an unexpected exit, so
it hot-restarted the loop every ~5s forever -- emitting tens of thousands of
"Skipped daemon start (lock already held)" activity-log rows (observed: 48,526
all-time) and re-attempting the file lock on every cycle.

The fix: ``run_in_loop()`` returns a sentinel whose ``stop_supervision`` flag is
truthy, and the supervisor honours that flag by stopping (not restarting).
"""
from __future__ import annotations

import asyncio



def test_run_in_loop_declines_with_stop_sentinel_when_lock_held(monkeypatch):
    """Lock held by another instance -> run_in_loop returns a stop-supervision
    sentinel and never enters the market loop."""
    from axiom import daemon

    # Another instance owns the lock.
    monkeypatch.setattr(daemon, "_acquire_daemon_lock", lambda: False)
    # Isolate from the real DB: the skip path calls log_activity.
    monkeypatch.setattr(daemon, "log_activity", lambda *a, **k: None)

    entered = {"market_loop": False}

    async def _fake_market_loop(state):  # pragma: no cover - must not run
        entered["market_loop"] = True

    monkeypatch.setattr(daemon, "async_market_loop", _fake_market_loop)

    result = asyncio.run(daemon.run_in_loop())

    assert getattr(result, "stop_supervision", False) is True
    assert entered["market_loop"] is False


def test_supervisor_stops_when_factory_declines(monkeypatch):
    """A factory that returns a stop-supervision sentinel must NOT be restarted:
    the supervisor returns after exactly one call."""
    from axiom import api

    calls = {"n": 0}

    class _Declined:
        stop_supervision = True

        def __str__(self) -> str:
            return "lock held by another instance"

    async def factory():
        calls["n"] += 1
        return _Declined()

    # If the loop ever restarts, the real >=1s sleep yields control and
    # wait_for cancels it -> TimeoutError (RED). The fix returns immediately.
    asyncio.run(
        asyncio.wait_for(
            api._supervise_background_loop("daemon-loop", factory, restart_seconds=0.01),
            timeout=3,
        )
    )

    assert calls["n"] == 1


def test_supervisor_still_restarts_on_plain_exit(monkeypatch):
    """A factory that returns plain None (unexpected exit) must still restart;
    once it later declines, the supervisor stops. Proves no regression to the
    existing restart-on-exit behaviour."""
    from axiom import api

    calls = {"n": 0}

    class _Declined:
        stop_supervision = True

    async def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # plain exit -> should restart
        return _Declined()  # then decline -> should stop

    asyncio.run(
        asyncio.wait_for(
            api._supervise_background_loop("daemon-loop", factory, restart_seconds=0.01),
            timeout=5,
        )
    )

    assert calls["n"] == 2
