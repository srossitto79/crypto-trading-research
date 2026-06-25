"""P3-T02 — versioning fields on QuantSkill, update_skill writes history rows."""
from __future__ import annotations

import tempfile

import pytest

from axiom import db as AXIOM_db
from axiom import quant_skills as qs


@pytest.fixture
def isolated_skills(tmp_path, monkeypatch):
    """Redirect SKILLS_DIR + AXIOM_HOME so SKILL.md and history both land in tmpdir."""
    skills_dir = tmp_path / "quant-skills"
    skills_dir.mkdir()
    (skills_dir / "_hypotheses").mkdir()
    (skills_dir / "_archived").mkdir()

    monkeypatch.setattr("axiom.quant_skills.SKILLS_DIR", skills_dir)
    monkeypatch.setattr("axiom.quant_skills.HYPOTHESES_DIR", skills_dir / "_hypotheses")
    monkeypatch.setattr("axiom.quant_skills.ARCHIVED_DIR", skills_dir / "_archived")

    # Fresh DB
    db_dir = tempfile.mkdtemp()
    monkeypatch.setenv("AXIOM_HOME", db_dir)
    if hasattr(AXIOM_db, "_DB_PATH"):
        AXIOM_db._DB_PATH = None  # type: ignore[attr-defined]
    if hasattr(AXIOM_db, "_init_db_done"):
        AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
    AXIOM_db.init_db()
    yield skills_dir


def _make_skill(name: str = "regime-trend-rsi") -> qs.QuantSkill:
    return qs.QuantSkill(
        name=name,
        description="rsi works on trend regime",
        skill_type="regime",
        metadata={
            "confidence": "0.50",
            "sample_size": "1",
            "regime": "TRENDING",
            "last_validated": "2026-04-25",
        },
        what_works=["rsi(14) below 30 buys work"],
        evidence=[{"recorded_at": "2026-04-25T00:00:00+00:00", "sharpe": 1.2}],
    )


def test_first_write_creates_v1_history_row(isolated_skills):
    skill = _make_skill()
    qs.write_skill(skill, evidence_task_id="task_v1")
    history = qs.list_skill_history(skill.name)
    assert len(history) == 1
    row = history[0]
    assert row["version"] == 1
    assert row["parent_version"] is None
    assert row["evidence_task_id"] == "task_v1"
    # v1 has no diff (no prior body)
    assert row["body_diff"] == ""


def test_update_skill_bumps_version_with_diff(isolated_skills):
    skill = _make_skill()
    qs.write_skill(skill)
    updated = qs.update_skill(
        skill.name,
        new_evidence={"recorded_at": "2026-04-26T00:00:00+00:00", "sharpe": 1.5},
        new_observations={"what_works": ["new observation about rsi divergence"]},
        evidence_task_id="task_v2",
        change_summary="Added rsi divergence observation",
    )
    assert updated is not None
    assert updated.version == 2
    assert updated.parent_version == 1

    history = qs.list_skill_history(skill.name)
    assert len(history) == 2
    # Sorted version DESC
    assert history[0]["version"] == 2
    assert history[1]["version"] == 1
    assert history[0]["parent_version"] == 1
    assert history[0]["change_summary"] == "Added rsi divergence observation"
    assert history[0]["evidence_task_id"] == "task_v2"
    assert "rsi divergence" in history[0]["body_diff"]


def test_repeated_updates_chain_lineage(isolated_skills):
    skill = _make_skill()
    qs.write_skill(skill)
    for i in range(2, 5):
        qs.update_skill(
            skill.name,
            new_evidence={"recorded_at": f"2026-04-{20+i}T00:00:00+00:00", "sharpe": 1.0 + i * 0.1},
            evidence_task_id=f"task_v{i}",
            change_summary=f"v{i} bump",
        )
    history = qs.list_skill_history(skill.name)
    assert [r["version"] for r in history] == [4, 3, 2, 1]
    assert [r["parent_version"] for r in history] == [3, 2, 1, None]


def test_get_skill_diff_spans_versions(isolated_skills):
    skill = _make_skill()
    qs.write_skill(skill)
    qs.update_skill(
        skill.name,
        new_evidence={"recorded_at": "2026-04-26T00:00:00+00:00", "sharpe": 1.3},
        new_observations={"what_works": ["v2-only line marker"]},
        change_summary="v2",
    )
    qs.update_skill(
        skill.name,
        new_evidence={"recorded_at": "2026-04-27T00:00:00+00:00", "sharpe": 1.4},
        new_observations={"what_works": ["v3-only line marker"]},
        change_summary="v3",
    )
    diff = qs.get_skill_diff(skill.name, 1, 3)
    assert diff
    assert "v2-only line marker" in diff
    assert "v3-only line marker" in diff


def test_get_skill_diff_same_version_empty(isolated_skills):
    skill = _make_skill()
    qs.write_skill(skill)
    assert qs.get_skill_diff(skill.name, 1, 1) == ""


def test_legacy_frontmatter_reads_as_v1(isolated_skills, tmp_path):
    """A pre-Phase-3 SKILL.md (no `version`, Axiom keys at top of metadata)
    must still load and report version=1."""
    skill_dir = isolated_skills / "legacy-skill"
    skill_dir.mkdir()
    (skill_dir / "references").mkdir()
    legacy = (
        "---\n"
        "name: legacy-skill\n"
        "description: legacy shape skill\n"
        "metadata:\n"
        "  type: regime\n"
        "  confidence: \"0.42\"\n"
        "  sample_size: \"7\"\n"
        "  regime: RANGE_BOUND\n"
        "---\n\n"
        "## What Works\n"
        "- legacy-format observation\n\n"
    )
    (skill_dir / "SKILL.md").write_text(legacy, encoding="utf-8")
    (skill_dir / "references" / "evidence.json").write_text("[]", encoding="utf-8")

    loaded = qs.read_skill("legacy-skill")
    assert loaded is not None
    assert loaded.version == 1
    assert loaded.confidence == 0.42
    assert loaded.regime == "RANGE_BOUND"
    assert "legacy-format observation" in loaded.what_works


def test_round_trip_legacy_to_v3(isolated_skills):
    """Legacy → write_skill → read should yield the v3 envelope shape on disk."""
    skill_dir = isolated_skills / "round-trip"
    skill_dir.mkdir()
    (skill_dir / "references").mkdir()
    legacy = (
        "---\n"
        "name: round-trip\n"
        "description: round trip test\n"
        "metadata:\n"
        "  type: indicator\n"
        "  confidence: \"0.55\"\n"
        "  sample_size: \"3\"\n"
        "---\n\n"
        "## What Works\n- alpha\n\n"
    )
    (skill_dir / "SKILL.md").write_text(legacy, encoding="utf-8")
    (skill_dir / "references" / "evidence.json").write_text("[]", encoding="utf-8")

    skill = qs.read_skill("round-trip")
    assert skill is not None
    qs.write_skill(skill)

    raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: 1" in raw or "version: '1'" in raw
    assert "axiom:" in raw  # nested under metadata.Axiom now
    # Re-read should produce equivalent fields
    reread = qs.read_skill("round-trip")
    assert reread is not None
    assert reread.confidence == 0.55
    assert reread.sample_size == 3
    assert reread.skill_type == "indicator"


def test_change_summary_default_when_empty(isolated_skills):
    skill = _make_skill()
    qs.write_skill(skill)
    updated = qs.update_skill(
        skill.name,
        new_evidence={"recorded_at": "2026-04-26T00:00:00+00:00", "sharpe": 1.5},
        evidence_task_id="task_xyz",
    )
    assert updated is not None
    assert "task_xyz" in updated.change_summary
