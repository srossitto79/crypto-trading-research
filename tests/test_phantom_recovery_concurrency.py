"""H-D4: recovery state machine is atomic under concurrent callers."""

from __future__ import annotations

import threading

import pytest

from axiom.db import (
    begin_phantom_recovery,
    get_db,
    get_db_immediate,
    get_phantom_recovery_state,
    init_db,
)


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


def _seed_strategy(strategy_id: str, stage: str = "backtesting") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO strategies (id, name, type, symbol, stage, status) "
            "VALUES (?, ?, 'test', 'BTC', ?, 'active')",
            (strategy_id, f"recovery test {strategy_id}", stage),
        )


def _cleanup_strategy(strategy_id: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM strategy_recovery_state WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_recovery_events WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))


def test_begin_phantom_recovery_is_serialized_under_concurrency():
    """Two threads calling begin_phantom_recovery for the same strategy
    must not both claim a new recovery. One wins, one is rejected because
    status already IN the claimed set.
    """
    strategy_id = "S9991"
    _seed_strategy(strategy_id)
    try:
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def _attempt():
            barrier.wait()  # release both threads simultaneously
            ok = begin_phantom_recovery(
                strategy_id,
                trigger="test",
                next_status="replay_running",
            )
            results.append(ok)

        t1 = threading.Thread(target=_attempt)
        t2 = threading.Thread(target=_attempt)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # Exactly one must have won the claim.
        assert results.count(True) == 1, f"expected exactly one winner, got {results}"
        assert results.count(False) == 1

        # State should show the surviving claim.
        state = get_phantom_recovery_state(strategy_id)
        assert state.get("status") == "replay_running"
        # attempt_count should be 1, not 2 — the loser's increment was rejected.
        assert int(state.get("attempt_count") or 0) == 1
    finally:
        _cleanup_strategy(strategy_id)


def test_get_db_immediate_commits_on_success():
    """The IMMEDIATE helper commits the write visibly to outer readers."""
    with get_db_immediate() as conn:
        conn.execute("INSERT INTO kv (key, value) VALUES ('hd4_test_key', '1')")
    with get_db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = 'hd4_test_key'").fetchone()
    assert row is not None and int(row["value"]) == 1
    with get_db() as conn:
        conn.execute("DELETE FROM kv WHERE key = 'hd4_test_key'")


def test_get_db_immediate_rolls_back_on_exception():
    """A raising block inside get_db_immediate must not leave partial writes."""
    with pytest.raises(RuntimeError):
        with get_db_immediate() as conn:
            conn.execute("INSERT INTO kv (key, value) VALUES ('hd4_rollback_key', '1')")
            raise RuntimeError("boom")
    with get_db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = 'hd4_rollback_key'").fetchone()
    assert row is None
