"""Phase 1 (P1-T06) — brain_decisions recording tests.

Asserts ``execute_brain_actions`` writes exactly one ``brain_decisions`` row
per call, populates the metadata columns, and links any ``agent_tasks`` rows
created by ``assign_task`` actions back via ``brain_decision_id``.
"""
from __future__ import annotations

import json

from axiom import brain as brain_mod
from axiom import brain_decisions as bd
from axiom.brain import BrainDecision, BrainTaskAction, BrainTransitionAction
from axiom.db import get_db


def _count_decisions() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM brain_decisions").fetchone()[0]


def _ensure_test_agent(agent_id: str = "quant-researcher") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (agent_id, agent_id.replace("-", " ").title(), agent_id),
        )


def test_record_decision_writes_row(AXIOM_db):
    decision_id = bd.record_decision(
        cycle_id="c1",
        situation_summary="BTC breaking 70k with rising volume",
        decision_json={"summary": "promote", "actions": []},
        prompt_hash="deadbeef",
    )
    assert decision_id > 0
    row = bd.get_decision(decision_id)
    assert row is not None
    assert row["cycle_id"] == "c1"
    assert row["situation_summary"].startswith("BTC breaking 70k")
    assert row["prompt_hash"] == "deadbeef"
    assert row["outcome_observed"] is None
    assert row["outcome_at"] is None
    parsed = json.loads(row["decision_json"])
    assert parsed["summary"] == "promote"


def test_situation_summary_truncates_at_cap(AXIOM_db):
    huge = "x" * (bd.SITUATION_SUMMARY_MAX_CHARS + 500)
    decision_id = bd.record_decision(
        cycle_id="c1",
        situation_summary=huge,
        decision_json={"actions": []},
    )
    row = bd.get_decision(decision_id)
    assert len(row["situation_summary"]) <= bd.SITUATION_SUMMARY_MAX_CHARS
    assert row["situation_summary"].endswith("…[truncated]")


def test_update_action_taken_round_trip(AXIOM_db):
    decision_id = bd.record_decision(
        cycle_id="c1",
        situation_summary="s",
        decision_json={"actions": []},
    )
    bd.update_action_taken(decision_id, [{"action": "assign_task", "task_id": 42}])
    row = bd.get_decision(decision_id)
    parsed = json.loads(row["action_taken"])
    assert parsed[0]["task_id"] == 42


def test_link_agent_task_sets_brain_decision_id(AXIOM_db):
    _ensure_test_agent("quant-researcher")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO agent_tasks (agent_id, type, title, description, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("quant-researcher", "research", "tlink", "tlink desc", "pending"),
        )
        task_id = int(cur.lastrowid)
    decision_id = bd.record_decision(
        cycle_id="c", situation_summary="s", decision_json={"actions": []}
    )
    bd.link_agent_task(task_id, decision_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT brain_decision_id FROM agent_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    assert int(row["brain_decision_id"]) == decision_id


def test_execute_brain_actions_writes_one_row_per_call(AXIOM_db, monkeypatch):
    _ensure_test_agent("quant-researcher")

    captured_task_ids: list[int] = []

    def fake_assign_task_direct(**kwargs):
        # Insert a real agent_tasks row so link_agent_task has a target.
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO agent_tasks (agent_id, type, title, description, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    kwargs["agent_id"],
                    kwargs["task_type"],
                    kwargs["title"],
                    kwargs["description"],
                    "pending",
                ),
            )
            task_id = int(cur.lastrowid)
        captured_task_ids.append(task_id)
        return task_id

    monkeypatch.setattr(brain_mod, "assign_task_direct", fake_assign_task_direct)

    decision = BrainDecision(
        summary="promote candidate",
        observations=["BTC strength"],
        actions=[
            BrainTaskAction(
                action="assign_task",
                agent_id="quant-researcher",
                task_type="research",
                title="Investigate funding skew",
                description="Look at BTC perp funding over last 24h.",
            )
        ],
    )

    before = _count_decisions()
    results = brain_mod.execute_brain_actions(
        decision,
        actor="brain",
        cycle_id="cycle-test",
        situation_summary="market summary",
        prompt_hash="hash-test",
    )
    after = _count_decisions()

    assert after == before + 1
    assert results[0]["status"] == "ok"
    assert results[0]["brain_decision_id"] is not None

    decision_id = results[0]["brain_decision_id"]
    row = bd.get_decision(decision_id)
    assert row["cycle_id"] == "cycle-test"
    assert row["prompt_hash"] == "hash-test"
    assert row["situation_summary"] == "market summary"
    assert row["action_taken"] is not None
    parsed = json.loads(row["action_taken"])
    assert parsed[0]["status"] == "ok"

    # FK linkage: agent_tasks row carries brain_decision_id.
    assert len(captured_task_ids) == 1
    with get_db() as conn:
        link = conn.execute(
            "SELECT brain_decision_id FROM agent_tasks WHERE id = ?",
            (captured_task_ids[0],),
        ).fetchone()
    assert int(link["brain_decision_id"]) == decision_id


def test_execute_brain_actions_rejects_unknown_agent(AXIOM_db, monkeypatch):
    """A task assigned to a non-existent agent_id is refused, not orphaned."""
    called: list[dict] = []

    def fake_assign_task_direct(**kwargs):
        called.append(kwargs)
        return 999

    monkeypatch.setattr(brain_mod, "assign_task_direct", fake_assign_task_direct)

    decision = BrainDecision(
        summary="assign to a ghost agent",
        observations=[],
        actions=[
            BrainTaskAction(
                action="assign_task",
                agent_id="nonexistent-agent",
                task_type="research",
                title="Ghost task",
                description="Should never be created.",
            )
        ],
    )
    results = brain_mod.execute_brain_actions(decision, actor="brain")

    assert called == []  # assign_task_direct must never be reached
    assert results[0]["status"] == "error"
    assert "unknown agent_id" in results[0]["error"]


def test_execute_brain_actions_allows_known_agent(AXIOM_db, monkeypatch):
    """Sanity: a real seeded agent still gets its task created."""
    _ensure_test_agent("quant-researcher")
    called: list[dict] = []

    def fake_assign_task_direct(**kwargs):
        called.append(kwargs)
        return 1234

    monkeypatch.setattr(brain_mod, "assign_task_direct", fake_assign_task_direct)

    decision = BrainDecision(
        summary="assign to a real agent",
        observations=[],
        actions=[
            BrainTaskAction(
                action="assign_task",
                agent_id="quant-researcher",
                task_type="research",
                title="Real task",
                description="ok",
            )
        ],
    )
    results = brain_mod.execute_brain_actions(decision, actor="brain")

    assert len(called) == 1
    assert results[0]["status"] == "ok"


def test_execute_brain_actions_uses_ambient_cycle_context(AXIOM_db, monkeypatch):
    _ensure_test_agent("quant-researcher")

    def fake_assign_task_direct(**kwargs):
        return 0  # simulate dedup / no new row

    monkeypatch.setattr(brain_mod, "assign_task_direct", fake_assign_task_direct)

    brain_mod._set_brain_cycle_context(
        cycle_id="ambient-cycle",
        situation_summary="ambient summary",
        prompt_hash="ambient-hash",
    )
    decision = BrainDecision(summary="noop", observations=[], actions=[])

    brain_mod.execute_brain_actions(decision, actor="brain")

    with get_db() as conn:
        row = conn.execute(
            "SELECT cycle_id, situation_summary, prompt_hash FROM brain_decisions "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["cycle_id"] == "ambient-cycle"
    assert row["situation_summary"] == "ambient summary"
    assert row["prompt_hash"] == "ambient-hash"


def test_execute_brain_actions_records_with_no_actions(AXIOM_db):
    decision = BrainDecision(summary="status quo", observations=["calm"], actions=[])
    before = _count_decisions()
    results = brain_mod.execute_brain_actions(
        decision,
        actor="brain",
        cycle_id="empty-cycle",
        situation_summary="nothing to do",
        prompt_hash="empty-hash",
    )
    assert results == []
    assert _count_decisions() == before + 1


def test_transition_action_records_decision_too(AXIOM_db, monkeypatch):
    monkeypatch.setattr(
        brain_mod,
        "transition_stage",
        lambda **kwargs: {"ok": True, **kwargs},
    )
    decision = BrainDecision(
        summary="archive",
        observations=[],
        actions=[
            BrainTransitionAction(
                action="transition_stage",
                strategy_id="S00001",
                to_stage="archived",
                reason="testing",
            )
        ],
    )
    before = _count_decisions()
    results = brain_mod.execute_brain_actions(
        decision, actor="brain", cycle_id="t-cycle", situation_summary="s", prompt_hash="h"
    )
    assert _count_decisions() == before + 1
    assert results[0]["status"] == "ok"
    assert results[0]["brain_decision_id"] is not None
