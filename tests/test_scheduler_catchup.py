"""Tests for Axiom.scheduler.apply_startup_catchup — collapse missed cycles."""

from datetime import datetime, timedelta, timezone

from axiom.db import get_db
from axiom.scheduler import apply_startup_catchup


def _insert_job(job_id: str, next_run_at: str, *, enabled: int = 1):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO scheduler_jobs
               (id, name, enabled, schedule_type, schedule_expr, timezone,
                command, payload, next_run_at)
               VALUES (?, ?, ?, 'interval', '60000', 'UTC', 'noop', NULL, ?)""",
            (job_id, job_id, enabled, next_run_at),
        )


def _read_next_run(job_id: str) -> str | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT next_run_at FROM scheduler_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return row["next_run_at"] if row else None


def test_no_jobs_returns_zero_counts(AXIOM_db):
    summary = apply_startup_catchup()
    assert summary["total_jobs"] == 0
    assert summary["fast_forwarded"] == 0


def test_fresh_job_not_touched(AXIOM_db):
    """A job whose next_run_at is in the future is left alone."""
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    _insert_job("test-job", future)
    summary = apply_startup_catchup(now=now)
    assert summary["fast_forwarded"] == 0
    assert _read_next_run("test-job") == future


def test_recently_overdue_job_not_collapsed(AXIOM_db):
    """Less than a minute late = normal slow tick, not catch-up territory."""
    now = datetime.now(timezone.utc)
    slightly_late = (now - timedelta(seconds=30)).isoformat()
    _insert_job("test-job", slightly_late)
    summary = apply_startup_catchup(now=now)
    assert summary["fast_forwarded"] == 0
    assert _read_next_run("test-job") == slightly_late


def test_long_overdue_job_collapsed_to_one_run(AXIOM_db):
    """A job 6 hours late should be fast-forwarded so it runs ONCE on next tick."""
    now = datetime.now(timezone.utc)
    six_hours_ago = (now - timedelta(hours=6)).isoformat()
    _insert_job("test-job", six_hours_ago)
    summary = apply_startup_catchup(now=now)
    assert summary["fast_forwarded"] == 1
    assert summary["stale_jobs"] == 1

    new_next_run = _read_next_run("test-job")
    assert new_next_run is not None
    parsed = datetime.fromisoformat(new_next_run)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    # Should be just before now, not 6 hours ago.
    assert (now - parsed) < timedelta(seconds=5)


def test_disabled_jobs_ignored(AXIOM_db):
    """Disabled jobs are not surfaced by get_enabled_jobs and not touched."""
    now = datetime.now(timezone.utc)
    long_ago = (now - timedelta(days=2)).isoformat()
    _insert_job("disabled-job", long_ago, enabled=0)
    summary = apply_startup_catchup(now=now)
    assert summary["fast_forwarded"] == 0
    # Untouched.
    assert _read_next_run("disabled-job") == long_ago


def test_mixed_jobs(AXIOM_db):
    """Mix of fresh / slightly-late / very-late: only the very-late get collapsed."""
    now = datetime.now(timezone.utc)
    _insert_job("fresh", (now + timedelta(minutes=5)).isoformat())
    _insert_job("slightly-late", (now - timedelta(seconds=30)).isoformat())
    _insert_job("very-late-1", (now - timedelta(hours=2)).isoformat())
    _insert_job("very-late-2", (now - timedelta(days=1)).isoformat())

    summary = apply_startup_catchup(now=now)
    assert summary["total_jobs"] == 4
    assert summary["fast_forwarded"] == 2
    assert summary["stale_jobs"] == 2


def test_job_with_null_next_run_skipped(AXIOM_db):
    """Jobs without next_run_at can't be fast-forwarded — leave alone."""
    _insert_job("no-next-run", "")
    summary = apply_startup_catchup()
    assert summary["fast_forwarded"] == 0
