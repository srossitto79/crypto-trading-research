from __future__ import annotations

from axiom.db import create_strategy_container, get_db
from axiom.gauntlet.settings import build_settings_snapshot
from axiom.gauntlet.store import create_or_get_workflow


def _strategy() -> str:
    with get_db() as conn:
        strategy_id, _display_id, _base_id = create_strategy_container(
            conn=conn,
            name="Router Test",
            type_="rsi_momentum",
            symbol="BTC/USDT",
            timeframe="1h",
            params={"rsi_period": 14},
            stage="gauntlet",
        )
    return strategy_id


def test_lifecycle_gauntlet_status_delegates_to_unified_projection(AXIOM_db):
    strategy_id = _strategy()
    workflow = create_or_get_workflow(
        strategy_id=strategy_id,
        created_by="pytest",
        settings_snapshot=build_settings_snapshot(),
    )

    from axiom.routers.lifecycle import read_gauntlet_status

    status = read_gauntlet_status(strategy_id)

    assert status["workflow_id"] == workflow["id"]
    assert "parameter_jitter" in status["tests"]
    assert "param_jitter" not in status["tests"]


def test_gauntlet_router_can_create_and_resume_workflow(AXIOM_db, monkeypatch):
    strategy_id = _strategy()
    monkeypatch.setattr(
        "axiom.gauntlet.engine.resume_workflow",
        lambda workflow_id, max_steps=1: {"ok": True, "workflow_id": workflow_id, "steps_run": max_steps},
    )

    from axiom.routers.gauntlet import create_strategy_workflow, resume_gauntlet_workflow

    created = create_strategy_workflow(strategy_id)
    resumed = resume_gauntlet_workflow(created["workflow_id"], max_steps=3)

    assert created["ok"] is True
    assert created["workflow_id"]
    assert resumed["steps_run"] == 3
