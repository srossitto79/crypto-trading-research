"""resume_workflow must stop advancing FURTHER steps once the tick's wall-clock
budget is spent, so a multi-step visit (max_steps>1) cannot run several heavy
steps past the scheduler job timeout and orphan a worker thread. The first step
always runs (the tick only claims a workflow before the deadline); the overrun
is bounded to a single in-flight step regardless of max_steps.
"""
from __future__ import annotations

import time

from axiom.db import create_strategy_container, get_db
from axiom.gauntlet.engine import resume_workflow
from axiom.gauntlet.settings import build_settings_snapshot
from axiom.gauntlet.store import create_or_get_workflow


def _strategy() -> str:
    with get_db() as conn:
        sid, _d, _b = create_strategy_container(
            conn=conn, name="Deadline Test", type_="rsi_momentum", symbol="ETH/USDT",
            timeframe="1h", params={"rsi_period": 14}, stage="quick_screen",
        )
    return sid


def _passing_runner(ran):
    def _runner(workflow, step):
        ran.append(step["step_key"])
        return {"status": "passed"}
    return _runner


def test_stops_after_one_step_when_deadline_already_passed(AXIOM_db):
    wf = create_or_get_workflow(
        strategy_id=_strategy(), created_by="pytest", settings_snapshot=build_settings_snapshot()
    )
    ran = []
    out = resume_workflow(
        wf["id"], max_steps=4, runner=_passing_runner(ran),
        deadline_monotonic=time.monotonic() - 1.0,  # already expired
    )
    assert out["steps_run"] == 1  # first step runs; deadline then breaks before step 2
    assert len(ran) == 1


def test_advances_multiple_steps_without_a_deadline(AXIOM_db):
    wf = create_or_get_workflow(
        strategy_id=_strategy(), created_by="pytest", settings_snapshot=build_settings_snapshot()
    )
    ran = []
    out = resume_workflow(wf["id"], max_steps=4, runner=_passing_runner(ran), deadline_monotonic=None)
    assert out["steps_run"] >= 2  # the throughput win: several steps in one visit


def test_future_deadline_does_not_curtail_the_visit(AXIOM_db):
    wf = create_or_get_workflow(
        strategy_id=_strategy(), created_by="pytest", settings_snapshot=build_settings_snapshot()
    )
    ran = []
    out = resume_workflow(
        wf["id"], max_steps=4, runner=_passing_runner(ran),
        deadline_monotonic=time.monotonic() + 600.0,  # plenty of budget
    )
    assert out["steps_run"] >= 2
