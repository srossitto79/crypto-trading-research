from __future__ import annotations

import json


def test_create_strategy_container_persists_hypothesis_id(AXIOM_db):
    from axiom.db import create_strategy_container, get_db
    from axiom.hypotheses import create_hypothesis

    hypothesis = create_hypothesis(
        title="Container lineage",
        market_thesis="Every new strategy should persist its parent hypothesis immediately.",
        mechanism="Insert hypothesis_id during container creation instead of patching later.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )

    with get_db() as conn:
        strategy_id, _, _ = create_strategy_container(
            conn=conn,
            name="ignored",
            type_="macd",
            symbol="BTC",
            timeframe="1h",
            params={"fast": 12, "slow": 26, "signal": 9},
            hypothesis_id=hypothesis["id"],
        )
        row = conn.execute(
            "SELECT hypothesis_id FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

    assert row is not None
    assert row["hypothesis_id"] == hypothesis["id"]


def test_autonomous_strategy_research_with_hypothesis_creation_skips_generic_brain_callback(AXIOM_db):
    from axiom.agents import runner
    from axiom.agents.manager import create_agent
    from axiom.db import get_db

    create_agent(
        agent_id="1",
        name="MiniMax Strategy Dev",
        role="strategy-developer",
    )

    task = {
        "id": 7282,
        "display_id": "T07282",
        "type": "research",
        "title": "Daily Research Ideation (exploration)",
    }
    input_data = {
        "origin_mode": "autonomous",
        "swarm_role": "strategy-developer",
        "research_contract": {"lane": "exploration"},
    }

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO task_audit_log (task_id, agent_id, tool_name, input_json, output_summary, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "T07282",
                "1",
                "create_hypothesis",
                json.dumps({"title": "Volatility regime-dependent switching"}),
                json.dumps({"ok": True, "hypothesis": {"id": "HYP-123"}}),
                42,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_audit_log (task_id, agent_id, tool_name, input_json, output_summary, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "T07282",
                "1",
                "record_data_gap",
                json.dumps({"title": "Funding history"}),
                json.dumps({"ok": True}),
                18,
            ),
        )

    assert (
        runner._should_queue_brain_callback_for_completed_task(
            agent_id="1",
            task=task,
            input_data=input_data,
        )
        is False
    )


def test_manual_or_nonresearch_tasks_still_queue_brain_callback(AXIOM_db):
    from axiom.agents import runner
    from axiom.agents.manager import create_agent

    create_agent(
        agent_id="1",
        name="MiniMax Strategy Dev",
        role="strategy-developer",
    )

    assert (
        runner._should_queue_brain_callback_for_completed_task(
            agent_id="1",
            task={"id": 90, "display_id": "T00090", "type": "research", "title": "Manual research"},
            input_data={"origin_mode": "manual"},
        )
        is True
    )
    assert (
        runner._should_queue_brain_callback_for_completed_task(
            agent_id="1",
            task={"id": 91, "display_id": "T00091", "type": "risk_audit", "title": "Audit"},
            input_data={"origin_mode": "autonomous"},
        )
        is True
    )
