from __future__ import annotations

from axiom.agents import runner
from axiom.db import get_db


def test_autonomous_research_completion_queues_follow_through_for_fresh_hypothesis(AXIOM_db, monkeypatch):
    queued: list[dict] = []

    monkeypatch.setattr(runner, "_agent_is_strategy_developer", lambda _agent_id: True)
    monkeypatch.setattr(
        runner,
        "_list_hypotheses_for_follow_through",
        lambda: [
            {
                "id": "HYP-new",
                "display_id": "H00359",
                "title": "Funding Rate Mean Reversion - 4h Edge",
                "status": "proposed",
                "manager_state": "active",
                "origin_agent_id": "1",
                "created_at": "2026-04-15T23:03:32+00:00",
            }
        ],
    )
    monkeypatch.setattr(runner, "_list_hypothesis_strategies_for_follow_through", lambda _hypothesis_id: [])
    monkeypatch.setattr(
        runner,
        "_assign_follow_through_task",
        lambda **kwargs: queued.append(kwargs) or 9001,
    )

    task = {
        "id": 42,
        "display_id": "T00042",
        "type": "research",
        "title": "Daily Research Ideation (exploitation)",
        "created_at": "2026-04-15T23:01:44+00:00",
    }
    input_data = {
        "origin_mode": "autonomous",
        "_channel": "chat",
        "follow_through_hypotheses": [],
    }

    with get_db() as conn:
        task_id = runner._queue_autonomous_research_follow_through_if_needed(
            conn,
            agent_id="1",
            task=task,
            input_data=input_data,
        )

    assert task_id == 9001
    assert queued
    assert queued[0]["title"] == "Strategy Candidates from H00359"
    assert queued[0]["agent_id"] == "strategy-developer"
    assert queued[0]["task_type"] == "develop_candidate"
    assert queued[0]["input_data"]["action_kind"] == "develop_candidate"
    assert queued[0]["input_data"]["crucible_id"] == "HYP-new"
    assert queued[0]["input_data"]["hypothesis_id"] == "HYP-new"
    assert queued[0]["input_data"]["source_task_display_id"] == "T00042"


def test_autonomous_research_completion_skips_duplicate_follow_through_tasks(AXIOM_db, monkeypatch):
    monkeypatch.setattr(runner, "_agent_is_strategy_developer", lambda _agent_id: True)
    monkeypatch.setattr(
        runner,
        "_list_hypotheses_for_follow_through",
        lambda: [
            {
                "id": "HYP-new",
                "display_id": "H00359",
                "title": "Funding Rate Mean Reversion - 4h Edge",
                "status": "proposed",
                "manager_state": "active",
                "origin_agent_id": "1",
                "created_at": "2026-04-15T23:03:32+00:00",
            }
        ],
    )
    monkeypatch.setattr(runner, "_list_hypothesis_strategies_for_follow_through", lambda _hypothesis_id: [])

    called: list[dict] = []
    monkeypatch.setattr(
        runner,
        "_assign_follow_through_task",
        lambda **kwargs: called.append(kwargs) or 9001,
    )

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks
                (agent_id, type, title, description, status, created_at, input_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "strategy-developer",
                "research",
                "Strategy Candidates from H00359",
                "Existing follow-through task",
                "pending",
                "2026-04-15T23:04:00+00:00",
                "{}",
            ),
        )

        task_id = runner._queue_autonomous_research_follow_through_if_needed(
            conn,
            agent_id="1",
            task={
                "id": 42,
                "display_id": "T00042",
                "type": "research",
                "title": "Daily Research Ideation (exploitation)",
                "created_at": "2026-04-15T23:01:44+00:00",
            },
            input_data={"origin_mode": "autonomous", "_channel": "chat"},
        )

    assert task_id is None
    assert called == []


def test_crucible_planner_completion_skips_legacy_follow_through(AXIOM_db, monkeypatch):
    queued: list[dict] = []

    monkeypatch.setattr(runner, "_agent_is_strategy_developer", lambda _agent_id: True)
    monkeypatch.setattr(
        runner,
        "_assign_follow_through_task",
        lambda **kwargs: queued.append(kwargs) or 9001,
    )

    with get_db() as conn:
        task_id = runner._queue_autonomous_research_follow_through_if_needed(
            conn,
            agent_id="strategy-developer",
            task={
                "id": 43,
                "display_id": "T00043",
                "type": "research",
                "title": "Refine crucible HYP-1",
                "created_at": "2026-04-15T23:01:44+00:00",
            },
            input_data={
                "origin_mode": "crucible_planner",
                "action_kind": "refine_crucible",
                "crucible_id": "HYP-1",
            },
        )

    assert task_id is None
    assert queued == []


def test_crucible_planner_completion_skips_brain_callback(monkeypatch):
    monkeypatch.setattr(runner, "_agent_is_strategy_developer", lambda _agent_id: True)

    should_queue = runner._should_queue_brain_callback_for_completed_task(
        agent_id="strategy-developer",
        task={"id": 43, "display_id": "T00043", "type": "research"},
        input_data={
            "origin_mode": "crucible_planner",
            "action_kind": "refine_crucible",
            "crucible_id": "HYP-1",
        },
    )

    assert should_queue is False
