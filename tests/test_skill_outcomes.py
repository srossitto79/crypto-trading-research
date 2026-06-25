"""P3-T05 — outcome closure cycle."""
from __future__ import annotations

import json
import tempfile

import pytest

from axiom import db as AXIOM_db
from axiom import quant_skills as qs
from axiom import skill_outcomes as so


@pytest.fixture
def env(tmp_path, monkeypatch):
    skills_dir = tmp_path / "quant-skills"
    skills_dir.mkdir()
    (skills_dir / "_hypotheses").mkdir()
    (skills_dir / "_archived").mkdir()
    monkeypatch.setattr("axiom.quant_skills.SKILLS_DIR", skills_dir)
    monkeypatch.setattr("axiom.quant_skills.HYPOTHESES_DIR", skills_dir / "_hypotheses")
    monkeypatch.setattr("axiom.quant_skills.ARCHIVED_DIR", skills_dir / "_archived")

    db_dir = tempfile.mkdtemp()
    monkeypatch.setenv("AXIOM_HOME", db_dir)
    if hasattr(AXIOM_db, "_DB_PATH"):
        AXIOM_db._DB_PATH = None  # type: ignore[attr-defined]
    if hasattr(AXIOM_db, "_init_db_done"):
        AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
    AXIOM_db.init_db()
    yield {"skills_dir": skills_dir, "db_dir": db_dir}


def _seed_skill(name: str, confidence: float = 0.5):
    skill = qs.QuantSkill(
        name=name,
        description=f"{name} description",
        skill_type="regime",
        metadata={
            "confidence": str(confidence),
            "sample_size": "5",
            "regime": "TRENDING",
            "last_validated": "2026-04-25",
        },
        what_works=["alpha"],
        evidence=[{"recorded_at": "2026-04-25T00:00:00+00:00", "sharpe": 1.0}],
    )
    qs.write_skill(skill)
    return skill


def _seed_task_with_citations(strategy_id: str, skills: list[str]) -> int:
    """Insert an agent_tasks row with cited_skills in output_data."""
    with AXIOM_db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO agent_tasks (agent_id, type, strategy_id, output_data, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "brain",
                "ideation",
                strategy_id,
                json.dumps({"cited_skills": skills}),
                "completed",
            ),
        )
        return int(cur.lastrowid)


def test_negative_outcome_decrements_two_skills(env):
    _seed_skill("regime-trend-rsi", confidence=0.6)
    _seed_skill("regime-trend-macd", confidence=0.7)
    _seed_task_with_citations("s-001", ["regime-trend-rsi", "regime-trend-macd"])

    events = so.record_outcome("s-001", "negative", "transition_stage:archived")
    assert len(events) == 2
    by_skill = {e["skill_name"]: e for e in events}
    assert by_skill["regime-trend-rsi"]["confidence_delta"] == pytest.approx(-0.05, abs=1e-6)
    assert by_skill["regime-trend-rsi"]["confidence_after"] == pytest.approx(0.55, abs=1e-6)
    assert by_skill["regime-trend-macd"]["confidence_after"] == pytest.approx(0.65, abs=1e-6)


def test_outcome_creates_history_rows(env):
    _seed_skill("regime-trend-rsi", confidence=0.6)
    _seed_task_with_citations("s-002", ["regime-trend-rsi"])

    so.record_outcome("s-002", "negative", "transition_stage:archived")
    history = qs.list_skill_history("regime-trend-rsi")
    # v1 (initial seed) + v2 (outcome closure write)
    assert any("Outcome closure" in r["change_summary"] for r in history)


def test_outcome_idempotent(env):
    _seed_skill("regime-trend-rsi", confidence=0.6)
    _seed_task_with_citations("s-003", ["regime-trend-rsi"])

    first = so.record_outcome("s-003", "negative", "transition_stage:archived")
    second = so.record_outcome("s-003", "negative", "transition_stage:archived")
    assert len(first) == 1
    assert len(second) == 0  # already recorded — skip


def test_confidence_clamps_at_floor(env):
    _seed_skill("regime-trend-rsi", confidence=0.02)
    _seed_task_with_citations("s-004", ["regime-trend-rsi"])

    events = so.record_outcome("s-004", "negative", "transition_stage:archived")
    assert events[0]["confidence_after"] == 0.0
    # actual delta clamped
    assert events[0]["confidence_delta"] == pytest.approx(-0.02, abs=1e-6)


def test_confidence_clamps_at_ceiling(env):
    _seed_skill("regime-trend-rsi", confidence=0.99)
    _seed_task_with_citations("s-005", ["regime-trend-rsi"])

    events = so.record_outcome("s-005", "positive", "transition_stage:live_graduated")
    assert events[0]["confidence_after"] == 1.0
    assert events[0]["confidence_delta"] == pytest.approx(0.01, abs=1e-6)


def test_no_citations_no_events(env):
    # Strategy has no agent_tasks rows linked.
    events = so.record_outcome("s-orphan", "negative", "transition_stage:archived")
    assert events == []


def test_invalid_outcome_raises(env):
    with pytest.raises(ValueError):
        so.record_outcome("s-001", "bogus", "trigger")  # type: ignore[arg-type]


def test_list_skill_outcomes_filters(env):
    _seed_skill("regime-trend-rsi", confidence=0.6)
    _seed_skill("regime-trend-macd", confidence=0.6)
    _seed_task_with_citations("s-A", ["regime-trend-rsi"])
    _seed_task_with_citations("s-B", ["regime-trend-macd"])

    so.record_outcome("s-A", "negative", "transition_stage:archived")
    so.record_outcome("s-B", "positive", "transition_stage:live_graduated")

    rsi_only = so.list_skill_outcomes(skill_name="regime-trend-rsi")
    assert len(rsi_only) == 1
    assert rsi_only[0]["outcome"] == "negative"

    s_b = so.list_skill_outcomes(strategy_id="s-B")
    assert len(s_b) == 1
    assert s_b[0]["skill_name"] == "regime-trend-macd"
