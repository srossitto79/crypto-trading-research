"""Tests for quant-skills API logic (isolated from FastAPI router imports).

Tests the quant_skills module functions that the API endpoint calls,
avoiding the heavy router import chain that requires a full DB.
"""

import pytest

from axiom.quant_skills import QuantSkill, list_skills, write_skill


@pytest.fixture(autouse=True)
def tmp_skills_dir(tmp_path, monkeypatch):
    """Redirect skill storage to a temp directory."""
    skills_dir = tmp_path / "quant-skills"
    hypotheses_dir = skills_dir / "_hypotheses"
    archived_dir = skills_dir / "_archived"
    skills_dir.mkdir()
    hypotheses_dir.mkdir()
    archived_dir.mkdir()

    monkeypatch.setattr("axiom.quant_skills.SKILLS_DIR", skills_dir)
    monkeypatch.setattr("axiom.quant_skills.HYPOTHESES_DIR", hypotheses_dir)
    monkeypatch.setattr("axiom.quant_skills.ARCHIVED_DIR", archived_dir)
    return skills_dir


def _make_skill(name="test-skill", confidence="0.80", regime="RANGE_BOUND", **kw):
    defaults = dict(
        name=name,
        description=f"Test skill {name}",
        skill_type="regime",
        metadata={"confidence": confidence, "sample_size": "10", "regime": regime, "last_validated": "2026-04-06"},
        what_works=["Something works"],
        what_doesnt_work=["Something fails"],
        evidence=[{"sharpe": 1.5}],
    )
    defaults.update(kw)
    return QuantSkill(**defaults)


def _simulate_api_call(regime=None, skill_type=None, limit=10, min_confidence=0.5):
    """Replicate the logic of GET /api/quant-skills without importing the router."""
    all_skills = list_skills(skill_type=skill_type)

    if regime:
        regime_upper = regime.upper()
        all_skills = [s for s in all_skills if not s.regime or s.regime.upper() == regime_upper]

    all_skills = [s for s in all_skills if s.confidence >= min_confidence]
    all_skills.sort(key=lambda s: s.confidence, reverse=True)
    top = all_skills[:max(1, min(limit, 50))]

    skills_out = []
    for s in top:
        skills_out.append({
            "name": s.name,
            "skill_type": s.skill_type,
            "confidence": s.confidence,
            "sample_size": s.sample_size,
            "regime": s.regime,
            "summary": s.description,
            "metadata": s.metadata,
        })

    return {
        "skills": skills_out,
        "meta": {
            "total_skills": len(list_skills()),
            "returned": len(skills_out),
        },
    }


def test_api_empty_state():
    result = _simulate_api_call()
    assert result["skills"] == []
    assert result["meta"]["total_skills"] == 0


def test_api_returns_skills(tmp_skills_dir):
    write_skill(_make_skill("skill-a", confidence="0.90"))
    write_skill(_make_skill("skill-b", confidence="0.60"))

    result = _simulate_api_call()
    assert len(result["skills"]) == 2
    assert result["skills"][0]["name"] == "skill-a"
    assert result["skills"][0]["confidence"] == 0.90


def test_api_filters_by_regime(tmp_skills_dir):
    write_skill(_make_skill("range-skill", regime="RANGE_BOUND"))
    write_skill(_make_skill("trend-skill", regime="TREND_UP"))

    result = _simulate_api_call(regime="RANGE_BOUND")
    names = [s["name"] for s in result["skills"]]
    assert "range-skill" in names
    assert "trend-skill" not in names


def test_api_filters_by_confidence(tmp_skills_dir):
    write_skill(_make_skill("high-conf", confidence="0.90"))
    write_skill(_make_skill("low-conf", confidence="0.30"))

    result = _simulate_api_call(min_confidence=0.5)
    assert len(result["skills"]) == 1
    assert result["skills"][0]["name"] == "high-conf"


def test_api_respects_limit(tmp_skills_dir):
    for i in range(5):
        write_skill(_make_skill(f"skill-{i}", confidence=f"0.{90 - i * 10}"))

    result = _simulate_api_call(limit=2, min_confidence=0.0)
    assert len(result["skills"]) == 2
    assert result["meta"]["total_skills"] == 5


def test_api_filters_by_skill_type(tmp_skills_dir):
    write_skill(_make_skill("regime-skill", skill_type="regime"))
    write_skill(_make_skill("failure-skill", skill_type="failure", confidence="0.80"))

    result = _simulate_api_call(skill_type="failure", min_confidence=0.0)
    assert len(result["skills"]) == 1
    assert result["skills"][0]["name"] == "failure-skill"
