from __future__ import annotations

import json

from axiom.brain import create_strategy
from axiom.db import get_db
from axiom.hypotheses import create_hypothesis


def _create_hypothesis() -> str:
    hypothesis = create_hypothesis(
        title="Momentum continuation after volatility compression",
        market_thesis="Compressed volatility can precede directional continuation.",
        mechanism="Breakouts after compression should carry short-term momentum.",
        why_now="Recent intraday regimes show repeated compression and expansion cycles.",
        lane="crucible",
        source_type="test",
        target_assets=["BTC/USDT"],
        target_timeframes=["1h"],
    )
    return str(hypothesis["id"])


def _insert_running_planner_task(
    *,
    display_id: str,
    agent_id: str,
    crucible_id: str,
    action_kind: str = "develop_candidate",
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks
                (agent_id, type, title, description, input_data, display_id, status)
            VALUES (?, 'develop_candidate', 'Develop candidate', 'Build a candidate strategy', ?, ?, 'running')
            """,
            (
                agent_id,
                json.dumps(
                    {
                        "origin_mode": "crucible_planner",
                        "action_kind": action_kind,
                        "crucible_id": crucible_id,
                        "hypothesis_id": crucible_id,
                    }
                ),
                display_id,
            ),
        )


def test_validate_candidate_strategy_creation_rejects_agent_without_planner_task(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-123",
        agent_id="strategy-developer",
        task_display_id="T0001",
    )

    assert result.allowed is False
    assert "planner-approved" in result.reason


def test_validate_candidate_strategy_creation_allows_running_planner_candidate_task(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    _insert_running_planner_task(
        display_id="T0001",
        agent_id="strategy-developer",
        crucible_id="HYP-123",
    )

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-123",
        agent_id="strategy-developer",
        task_display_id="T0001",
    )

    assert result.allowed is True
    assert result.reason == ""


def test_validate_candidate_strategy_creation_rejects_mismatched_requested_hypothesis(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    _insert_running_planner_task(
        display_id="T0002",
        agent_id="strategy-developer",
        crucible_id="HYP-parent",
    )

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-parent",
        hypothesis_id="HYP-child",
        agent_id="strategy-developer",
        task_display_id="T0002",
    )

    assert result.allowed is False
    assert "planner-approved" in result.reason


def test_validate_candidate_strategy_creation_rejects_wrong_agent_running_task(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    _insert_running_planner_task(
        display_id="T0003",
        agent_id="other-agent",
        crucible_id="HYP-123",
    )

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-123",
        agent_id="strategy-developer",
        task_display_id="T0003",
    )

    assert result.allowed is False
    assert "current agent" in result.reason


def test_validate_candidate_strategy_creation_allows_matching_payload_hypothesis_alias(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks
                (agent_id, type, title, description, input_data, display_id, status)
            VALUES (?, 'develop_candidate', 'Develop candidate', 'Build a candidate strategy', ?, ?, 'running')
            """,
            (
                "strategy-developer",
                json.dumps(
                    {
                        "origin_mode": "crucible_planner",
                        "action_kind": "develop_candidate",
                        "crucible_id": "CRUCIBLE-123",
                        "hypothesis_id": "HYP-123",
                    }
                ),
                "T0002",
            ),
        )

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-123",
        agent_id="strategy-developer",
        task_display_id="T0002",
    )

    assert result.allowed is True


def test_validate_candidate_strategy_creation_allows_hypothesis_promotion_loop_candidate(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks
                (agent_id, type, title, description, input_data, display_id, status)
            VALUES (?, 'develop_candidate', 'Advance hypothesis', 'Build a candidate strategy', ?, ?, 'running')
            """,
            (
                "strategy-developer",
                json.dumps(
                    {
                        "origin_mode": "hypothesis_promotion_loop",
                        "action_kind": "develop_candidate",
                        "crucible_id": "HYP-456",
                        "hypothesis_id": "HYP-456",
                    }
                ),
                "T0004",
            ),
        )

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-456",
        agent_id="strategy-developer",
        task_display_id="T0004",
    )

    assert result.allowed is True


def test_validate_candidate_strategy_creation_recovers_missing_task_display_id(AXIOM_db):
    from axiom.crucible_tasks import validate_candidate_strategy_creation

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks
                (agent_id, type, title, description, input_data, display_id, status)
            VALUES (?, 'develop_candidate', 'Advance hypothesis', 'Build a candidate strategy', ?, ?, 'running')
            """,
            (
                "strategy-developer",
                json.dumps(
                    {
                        "origin_mode": "hypothesis_promotion_loop",
                        "action_kind": "develop_candidate",
                        "crucible_id": "HYP-789",
                        "hypothesis_id": "HYP-789",
                    }
                ),
                "T0005",
            ),
        )

    result = validate_candidate_strategy_creation(
        crucible_id="HYP-789",
        agent_id="strategy-developer",
        task_display_id="",
        hypothesis_id="HYP-789",
    )

    assert result.allowed is True
    assert result.crucible_id == "HYP-789"
    assert result.hypothesis_id == "HYP-789"


def test_brain_create_strategy_persists_strategy_provenance(AXIOM_db, monkeypatch):
    hypothesis_id = _create_hypothesis()
    monkeypatch.setattr("axiom.lab_features.is_pipeline_saturated", lambda: (False, 0, ""))

    result = create_strategy(
        strategy_id="provenance-strategy-1",
        hypothesis_id=hypothesis_id,
        name="Provenance MACD",
        strategy_type="macd",
        symbol="BTC/USDT",
        params={"fast": 5, "slow": 13, "signal": 3},
        model="openai",
        model_id="gpt-5.2",
        origin_crucible_id=hypothesis_id,
        origin_agent_id="strategy-developer",
        origin_task_id="T0001",
        origin_model=None,
    )

    assert "error" not in result
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT origin_crucible_id, origin_agent_id, origin_task_id, origin_model
            FROM strategies
            WHERE id = ?
            """,
            (result["id"],),
        ).fetchone()

    assert dict(row) == {
        "origin_crucible_id": hypothesis_id,
        "origin_agent_id": "strategy-developer",
        "origin_task_id": "T0001",
        "origin_model": "gpt-5.2",
    }
