"""Scheduler and compatibility wiring for the crucible planner."""

from __future__ import annotations

import asyncio
import json

from axiom.db import get_db
from axiom.scheduler import run_job, seed_AXIOM_jobs


def _scheduler_row(job_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, enabled, schedule_type, schedule_expr, command, payload "
            "FROM scheduler_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_seed_AXIOM_jobs_enables_crucible_planner_and_disables_superseded_jobs(AXIOM_db):
    seed_AXIOM_jobs()

    planner = _scheduler_row("Axiom-crucible-planner")
    payload = json.loads(planner["payload"] or "{}")

    assert planner["enabled"] == 1
    assert planner["schedule_type"] == "interval"
    assert planner["schedule_expr"] == str(5 * 60 * 1000)
    assert planner["command"] == "crucible-planner"
    assert payload == {"kind": "crucible_planner", "limit": 5}

    assert _scheduler_row("Axiom-ideation-daily")["enabled"] == 0
    # The Daily Coding Cycle is retired — not seeded at all (not merely disabled).
    with get_db() as conn:
        coding_row = conn.execute(
            "SELECT id FROM scheduler_jobs WHERE id = 'Axiom-coding-daily'"
        ).fetchone()
    assert coding_row is None

    assert _scheduler_row("Axiom-hypothesis-promotion-loop")["enabled"] == 1


def test_run_job_dispatches_crucible_planner_kind(monkeypatch, AXIOM_db):
    calls: list[int] = []

    def _stub_crucible_planner_cycle(*, limit: int = 3):
        calls.append(limit)
        return {"planned": 1, "assigned": 1}

    import axiom.crucible_planner as crucible_planner_mod

    monkeypatch.setattr(
        crucible_planner_mod,
        "run_crucible_planner_cycle",
        _stub_crucible_planner_cycle,
    )

    status, error = asyncio.run(
        run_job(
            {
                "id": "test-crucible-planner",
                "name": "Test Crucible Planner",
                "command": "crucible-planner",
                "payload": json.dumps({"kind": "crucible_planner", "limit": 7}),
            }
        )
    )

    assert status == "ok"
    assert error is None
    assert calls == [7]


def test_brain_assign_research_cycle_delegates_to_crucible_planner(monkeypatch, AXIOM_db):
    calls: list[int] = []

    def _stub_crucible_planner_cycle(*, limit: int = 3):
        calls.append(limit)
        return {"planned": 2, "assigned": 2}

    def _legacy_assign_task(*args, **kwargs):
        raise AssertionError("assign_research_cycle should not fan out legacy agent_tasks")

    import axiom.brain as brain_mod
    import axiom.crucible_planner as crucible_planner_mod

    monkeypatch.setattr(
        crucible_planner_mod,
        "run_crucible_planner_cycle",
        _stub_crucible_planner_cycle,
    )
    monkeypatch.setattr(brain_mod, "assign_task", _legacy_assign_task)

    result = brain_mod.assign_research_cycle()

    assert result == {"planned": 2, "assigned": 2}
    assert calls == [3]
