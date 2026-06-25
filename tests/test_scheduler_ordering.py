from datetime import datetime, timedelta, timezone

from axiom.scheduler import _get_due_jobs


def test_get_due_jobs_orders_oldest_due_first():
    now = datetime.now(timezone.utc)
    jobs = [
        {"id": "future", "next_run_at": (now + timedelta(minutes=5)).isoformat()},
        {"id": "newer_due", "next_run_at": (now - timedelta(minutes=5)).isoformat()},
        {"id": "older_due", "next_run_at": (now - timedelta(minutes=30)).isoformat()},
        {"id": "invalid", "next_run_at": "not-a-date"},
    ]

    due_jobs = _get_due_jobs(jobs, now)

    assert [job["id"] for _, job in due_jobs] == ["older_due", "newer_due"]
