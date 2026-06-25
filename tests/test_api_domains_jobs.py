from __future__ import annotations

from datetime import datetime, timezone

from axiom.api_domains import jobs as jobs_domain
from axiom.db import get_db


def test_jobs_compat_lists_and_fetches_task_jobs(AXIOM_db):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO tasks (type, payload, status, created_at, completed_at, error)
            VALUES ('brain_invoke', '{"job_id":"job-123","progress":"50%"}', 'completed', ?, ?, NULL)
            """,
            (now, now),
        )

    jobs = jobs_domain.get_jobs_compat(limit=10)
    job = jobs_domain.get_job_compat("job-123")
    cancel_payload = jobs_domain.cancel_job_compat("job-123")

    assert jobs[0]["id"] == "job-123"
    assert job["progress"] == "50%"
    assert cancel_payload["status"] == "ok"
