"""P3-T04 — three-level progressive disclosure tools."""
from __future__ import annotations

import tempfile

import pytest

from axiom import db as AXIOM_db
from axiom import quant_skills as qs


@pytest.fixture
def isolated_skills(tmp_path, monkeypatch):
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
    yield skills_dir


def _seed(name: str, n: int = 100):
    """Seed N skills cheaply so the lister has a realistic catalog to summarize."""
    for i in range(n):
        skill = qs.QuantSkill(
            name=f"{name}-{i:03d}",
            description=f"insight number {i}",
            skill_type="regime",
            metadata={
                "confidence": "0.50",
                "sample_size": "5",
                "regime": "TRENDING" if i % 2 else "RANGE_BOUND",
                "last_validated": "2026-04-25",
            },
            what_works=[f"thing {i} works"],
            evidence=[{"recorded_at": "2026-04-25T00:00:00+00:00", "sharpe": 1.0}],
        )
        qs.write_skill(skill)


def test_quant_skills_list_metadata_only_under_3k_tokens(isolated_skills):
    _seed("regime-test", n=100)
    rows = qs.quant_skills_list()
    assert len(rows) == 100
    # No body fields
    for r in rows:
        assert "what_works" not in r
        assert "what_doesnt_work" not in r
        assert "evidence" not in r
        assert "description" not in r  # description deferred to L2 view
        assert "version" in r
        assert "confidence" in r
    # Token guardrail — spec target ~2k tokens for the catalog
    tokens = qs._estimate_tokens(rows)
    assert tokens < 3000, f"lister output is {tokens} tokens; target <3000"


def test_quant_skill_view_full_returns_detail(isolated_skills):
    _seed("regime-detail", n=1)
    full = qs.quant_skill_view("regime-detail-000")
    assert full is not None
    assert full["name"] == "regime-detail-000"
    assert "what_works" in full
    assert "evidence" in full
    assert "version" in full
    assert "parent_version" in full
    assert "change_summary" in full


def test_quant_skill_view_section_evidence(isolated_skills):
    _seed("regime-section", n=1)
    section = qs.quant_skill_view("regime-section-000", "evidence")
    assert section is not None
    assert "evidence" in section
    assert "what_works" not in section


def test_quant_skill_view_section_what_works(isolated_skills):
    _seed("regime-section", n=1)
    section = qs.quant_skill_view("regime-section-000", "what_works")
    assert section == {"what_works": ["thing 0 works"]}


def test_quant_skill_view_section_history(isolated_skills):
    _seed("regime-history", n=1)
    qs.update_skill(
        "regime-history-000",
        new_evidence={"recorded_at": "2026-04-26T00:00:00+00:00", "sharpe": 1.1},
        change_summary="bumped",
    )
    history = qs.quant_skill_view("regime-history-000", "history")
    assert isinstance(history, list)
    assert len(history) >= 2
    # version DESC
    assert history[0]["version"] >= history[-1]["version"]


def test_quant_skill_view_nonexistent_returns_none(isolated_skills):
    assert qs.quant_skill_view("nonexistent") is None
    # history section on nonexistent returns empty list
    assert qs.quant_skill_view("nonexistent", "history") == []


def test_quant_skill_view_unknown_section_raises(isolated_skills):
    _seed("regime-bogus", n=1)
    with pytest.raises(ValueError):
        qs.quant_skill_view("regime-bogus-000", "bogus_section_name")
