"""Tests for Axiom.maintenance terminal queue-row pruning (failed_retention_hours).

Covers FIX #27: wire the previously-unconsumed ``failed_retention_hours`` knob so
``run_db_maintenance`` prunes definitively-terminal agent_tasks/tasks rows, uses a
strictly longer + recovery-aware window for ``failed`` rows, and NEVER touches
``interrupted`` or in-flight rows. Defaults must be effectively safe (only very
old rows ever go).
"""

from datetime import datetime, timedelta, timezone

from axiom.db import get_db
from axiom.maintenance import (
    DEFAULT_FAILED_RETENTION_HOURS,
    _FAILED_WINDOW_MULTIPLIER,
    prune_terminal_task_rows,
    run_db_maintenance,
)


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


def _insert_agent_task(
    *, status: str, completed_at: str | None, error: str | None = None,
    title: str = "t", agent_id: str = "agent-test", strategy_id: str | None = None,
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO agent_tasks
               (agent_id, type, title, description, status, started_at,
                completed_at, error, strategy_id)
               VALUES (?, 'general', ?, '', ?, ?, ?, ?, ?)""",
            (agent_id, title, status, completed_at, completed_at, error, strategy_id),
        )
        return int(cur.lastrowid)


def _insert_task(
    *, status: str, completed_at: str | None, error: str | None = None,
    type_: str = "general",
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (type, status, completed_at, error)
               VALUES (?, ?, ?, ?)""",
            (type_, status, completed_at, error),
        )
        return int(cur.lastrowid)


def _exists_agent(task_id: int) -> bool:
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM agent_tasks WHERE id=?", (task_id,)
        ).fetchone() is not None


def _exists_task(task_id: int) -> bool:
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM tasks WHERE id=?", (task_id,)
        ).fetchone() is not None


# --- default safety ---------------------------------------------------------

def test_default_window_is_noop_for_recent_terminal_rows(AXIOM_db):
    """At the default 72h window, recent terminal rows survive (effectively off)."""
    done = _insert_agent_task(status="done", completed_at=_ts(1))
    cancelled = _insert_task(status="cancelled", completed_at=_ts(10))
    deleted = prune_terminal_task_rows(DEFAULT_FAILED_RETENTION_HOURS)
    assert deleted == 0
    assert _exists_agent(done)
    assert _exists_task(cancelled)


def test_run_db_maintenance_default_settings_does_not_prune_recent(AXIOM_db):
    """The wired job is a no-op for fresh queue rows under default settings."""
    done = _insert_agent_task(status="done", completed_at=_ts(2))
    summary = run_db_maintenance({})
    assert summary["terminal_task_rows"] == 0
    assert _exists_agent(done)


def test_zero_disables_pruning(AXIOM_db):
    old = _insert_agent_task(status="done", completed_at=_ts(10_000))
    assert prune_terminal_task_rows(0) == 0
    assert _exists_agent(old)


# --- definitive terminal pruning -------------------------------------------

def test_old_definitive_terminal_rows_are_pruned(AXIOM_db):
    keep = _insert_agent_task(status="done", completed_at=_ts(1))
    drop_done = _insert_agent_task(status="done", completed_at=_ts(100))
    drop_completed = _insert_agent_task(status="completed", completed_at=_ts(100))
    drop_cancelled = _insert_task(status="cancelled", completed_at=_ts(100))
    keep_task = _insert_task(status="cancelled", completed_at=_ts(1))

    deleted = prune_terminal_task_rows(72)
    assert deleted == 3
    assert _exists_agent(keep)
    assert not _exists_agent(drop_done)
    assert not _exists_agent(drop_completed)
    assert not _exists_task(drop_cancelled)
    assert _exists_task(keep_task)


def test_null_completed_at_is_never_pruned(AXIOM_db):
    """A terminal row with no completion timestamp is left alone (can't age it)."""
    row = _insert_agent_task(status="done", completed_at=None)
    assert prune_terminal_task_rows(72) == 0
    assert _exists_agent(row)


# --- never-prune statuses ---------------------------------------------------

def test_interrupted_rows_are_never_pruned(AXIOM_db):
    """``interrupted`` rows are re-pended on app restart — must survive pruning."""
    row = _insert_agent_task(status="interrupted", completed_at=_ts(10_000))
    assert prune_terminal_task_rows(1) == 0
    assert _exists_agent(row)


def test_inflight_rows_are_never_pruned(AXIOM_db):
    pending = _insert_agent_task(status="pending", completed_at=_ts(10_000))
    running = _insert_agent_task(status="running", completed_at=_ts(10_000))
    blocked = _insert_agent_task(status="blocked", completed_at=_ts(10_000))
    assert prune_terminal_task_rows(1) == 0
    assert _exists_agent(pending)
    assert _exists_agent(running)
    assert _exists_agent(blocked)


# --- failed rows: longer window + recovery-aware ----------------------------

def test_failed_uses_longer_window_than_definitive(AXIOM_db):
    """A failed row aged past the terminal window but inside the failed window survives."""
    # 100h old: past the 72h terminal window, but inside the failed window
    # (72h * multiplier). With a non-recoverable error it would otherwise be
    # eligible — proving the longer window, not the error filter, protects it.
    inside = _insert_agent_task(
        status="failed", completed_at=_ts(100), error="ValueError: bad params"
    )
    assert prune_terminal_task_rows(72) == 0
    assert _exists_agent(inside)


def test_failed_with_nonrecoverable_error_pruned_past_failed_window(AXIOM_db):
    failed_window_h = 72 * _FAILED_WINDOW_MULTIPLIER
    drop = _insert_agent_task(
        status="failed",
        completed_at=_ts(failed_window_h + 24),
        error="ValueError: deterministic strategy bug",
    )
    deleted = prune_terminal_task_rows(72)
    assert deleted == 1
    assert not _exists_agent(drop)


def test_failed_with_recoverable_error_is_never_pruned(AXIOM_db):
    """Rows recovery would re-queue (rate-limit / transient) are never deleted,
    even when far older than the failed window."""
    failed_window_h = 72 * _FAILED_WINDOW_MULTIPLIER
    rate_limited = _insert_agent_task(
        status="failed",
        completed_at=_ts(failed_window_h + 10_000),
        error="HTTP 429 Too Many Requests",
    )
    transient = _insert_task(
        status="failed",
        completed_at=_ts(failed_window_h + 10_000),
        error="ReadTimeout: provider unavailable",
        type_="brain_invoke",
    )
    deleted = prune_terminal_task_rows(72)
    assert deleted == 0
    assert _exists_agent(rate_limited)
    assert _exists_task(transient)


def test_recovery_protected_rows_do_not_block_other_deletions(AXIOM_db):
    """A recoverable failed row interleaved with deletable rows must not stop the
    page-forward scan from reaching the deletable ones."""
    failed_window_h = 72 * _FAILED_WINDOW_MULTIPLIER
    protected = _insert_agent_task(
        status="failed",
        completed_at=_ts(failed_window_h + 5_000),
        error="rate limit exceeded",
    )
    deletable = _insert_agent_task(
        status="failed",
        completed_at=_ts(failed_window_h + 5_000),
        error="AssertionError: bug",
    )
    # Force tiny batches so the protected row would land in its own page.
    deleted = prune_terminal_task_rows(72, batch=1, max_batches=10)
    assert deleted == 1
    assert _exists_agent(protected)
    assert not _exists_agent(deletable)


# --- FTS trigger sanity -----------------------------------------------------

def test_agent_tasks_fts_stays_consistent_after_prune(AXIOM_db):
    """The AFTER-DELETE FTS trigger must fire cleanly and keep the index queryable."""
    drop = _insert_agent_task(
        status="done", completed_at=_ts(1000), title="prunable-needle"
    )
    keep = _insert_agent_task(
        status="done", completed_at=_ts(1), title="survivor-haystack"
    )
    prune_terminal_task_rows(72)
    assert not _exists_agent(drop)
    with get_db() as conn:
        hits = conn.execute(
            "SELECT rowid FROM agent_tasks_fts WHERE agent_tasks_fts MATCH 'needle'"
        ).fetchall()
        assert hits == []
        survivors = conn.execute(
            "SELECT rowid FROM agent_tasks_fts WHERE agent_tasks_fts MATCH 'haystack'"
        ).fetchall()
        assert [r["rowid"] for r in survivors] == [keep]
