"""Tests for the Axiom-stale-triage scheduler job registration."""
from __future__ import annotations


def test_stale_triage_job_registered():
    """Axiom-stale-triage must be registered with kind='stale_triage'."""
    from axiom.db import init_db
    from axiom.scheduler import seed_AXIOM_jobs, get_jobs

    init_db()
    seed_AXIOM_jobs()

    jobs = {j["id"]: j for j in get_jobs()}
    assert "Axiom-stale-triage" in jobs
    payload = jobs["Axiom-stale-triage"].get("payload") or {}
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload.get("kind") == "stale_triage"
    assert int(payload.get("days", 0)) >= 1
