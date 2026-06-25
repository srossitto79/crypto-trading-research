from __future__ import annotations


def test_assign_research_cycle_delegates_to_crucible_planner(monkeypatch, AXIOM_db):
    from axiom import brain
    import axiom.crucible_planner as crucible_planner_mod

    calls: list[int] = []

    def _stub_crucible_planner_cycle(*, limit: int = 3):
        calls.append(limit)
        return {"planned": 3, "assigned": 3}

    def _legacy_assign_task(*args, **kwargs):
        raise AssertionError("assign_research_cycle should not create legacy swarm agent_tasks")

    monkeypatch.setattr(
        crucible_planner_mod,
        "run_crucible_planner_cycle",
        _stub_crucible_planner_cycle,
    )
    monkeypatch.setattr(brain, "assign_task", _legacy_assign_task)

    result = brain.assign_research_cycle()

    assert result == {"planned": 3, "assigned": 3}
    assert calls == [3]
