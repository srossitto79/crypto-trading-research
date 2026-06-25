"""P3-T07 — propose_skill_update Brain tool + skill_update_proposal approval."""
from __future__ import annotations

import importlib
import json

import pytest

from axiom import db as AXIOM_db_mod
from axiom import quant_skills as qs


def _load_tools_brain():
    return importlib.import_module("axiom.agents.tools_brain")


@pytest.fixture
def env(tmp_path, monkeypatch, AXIOM_db):
    skills_dir = tmp_path / "quant-skills"
    skills_dir.mkdir()
    (skills_dir / "_hypotheses").mkdir()
    (skills_dir / "_archived").mkdir()
    monkeypatch.setattr("axiom.quant_skills.SKILLS_DIR", skills_dir)
    monkeypatch.setattr("axiom.quant_skills.HYPOTHESES_DIR", skills_dir / "_hypotheses")
    monkeypatch.setattr("axiom.quant_skills.ARCHIVED_DIR", skills_dir / "_archived")
    yield skills_dir


def _seed_skill(name: str = "regime-trend-rsi"):
    skill = qs.QuantSkill(
        name=name,
        description="initial description",
        skill_type="regime",
        metadata={
            "confidence": "0.6",
            "sample_size": "5",
            "regime": "TRENDING",
            "last_validated": "2026-04-25",
        },
        what_works=["existing bullet"],
        what_doesnt_work=[],
        evidence=[{"recorded_at": "2026-04-25T00:00:00+00:00", "sharpe": 1.0}],
    )
    qs.write_skill(skill)
    return skill


def test_propose_skill_update_registered_for_brain(env):
    _load_tools_brain()
    from axiom.agents.tool_registry import get_tools_for_agent

    names = {t["name"] for t in get_tools_for_agent("brain")}
    assert "propose_skill_update" in names


def test_propose_skill_update_not_visible_to_quant(env):
    _load_tools_brain()
    from axiom.agents.tool_registry import get_tools_for_agent

    for role in ("quant-researcher", "strategy-developer", "execution-trader"):
        names = {t["name"] for t in get_tools_for_agent(role)}
        assert "propose_skill_update" not in names, f"leaked to {role}"


def test_propose_creates_pending_approval(env):
    tools_brain = _load_tools_brain()
    _seed_skill()

    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "regime-trend-rsi",
        "rationale": "Outcome closure suggests adding bullet about volume",
        "add_what_works": ["volume confirmation reduces false signals"],
        "metadata_updates": {"regime": "TRENDING_HIGH_VOL"},
    }))

    assert out["ok"] is True
    assert out["skill_name"] == "regime-trend-rsi"
    assert out["current_version"] >= 1
    assert isinstance(out["approval_id"], int)

    # The approval row exists with correct shape
    with AXIOM_db_mod.get_db() as conn:
        row = conn.execute(
            "SELECT approval_type, target_type, target_id, status, payload "
            "FROM approvals WHERE id = ?",
            (out["approval_id"],),
        ).fetchone()
    assert row is not None
    assert row["approval_type"] == "skill_update_proposal"
    assert row["target_type"] == "quant_skill"
    assert row["target_id"] == "regime-trend-rsi"
    assert row["status"] == "pending_approval"

    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    assert payload["add_what_works"] == ["volume confirmation reduces false signals"]
    assert payload["metadata_updates"] == {"regime": "TRENDING_HIGH_VOL"}


def test_propose_rejects_missing_skill(env):
    tools_brain = _load_tools_brain()
    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "nonexistent",
        "rationale": "test",
    }))
    assert out["ok"] is False
    assert out["error"] == "skill_not_found"


def test_propose_rejects_no_changes(env):
    tools_brain = _load_tools_brain()
    _seed_skill()
    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "regime-trend-rsi",
        "rationale": "I'm just going to think hard about this",
    }))
    assert out["ok"] is False
    assert out["error"] == "no_changes_proposed"


def test_propose_strips_protected_metadata(env):
    tools_brain = _load_tools_brain()
    _seed_skill()
    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "regime-trend-rsi",
        "rationale": "should not let Brain hand-set confidence",
        "metadata_updates": {"confidence": "0.99", "sample_size": "9999", "regime": "TRENDING"},
    }))
    assert out["ok"] is True

    with AXIOM_db_mod.get_db() as conn:
        row = conn.execute(
            "SELECT payload FROM approvals WHERE id = ?",
            (out["approval_id"],),
        ).fetchone()
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    # Protected fields are stripped
    assert "confidence" not in payload["metadata_updates"]
    assert "sample_size" not in payload["metadata_updates"]
    assert payload["metadata_updates"]["regime"] == "TRENDING"


def test_approve_skill_update_applies_and_bumps_version(env):
    tools_brain = _load_tools_brain()
    _seed_skill()

    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "regime-trend-rsi",
        "rationale": "add volume bullet",
        "add_what_works": ["volume confirmation"],
        "proposed_description": "revised description",
    }))
    approval_id = out["approval_id"]

    from axiom.control_plane.approvals import post_approve_approval
    from axiom.control_plane.models import ApprovalDecisionBody

    result = post_approve_approval(
        approval_id,
        ApprovalDecisionBody(actor="operator", reason="LGTM", feedback=None),
    )
    assert result["ok"] is True
    assert result["skill_name"] == "regime-trend-rsi"
    assert result["new_version"] == result["previous_version"] + 1

    refreshed = qs.read_skill("regime-trend-rsi")
    assert refreshed is not None
    assert refreshed.description == "revised description"
    assert "volume confirmation" in refreshed.what_works
    assert "existing bullet" in refreshed.what_works  # preserved
    assert refreshed.version == result["new_version"]

    history = qs.list_skill_history("regime-trend-rsi")
    assert any("Approved skill update" in r["change_summary"] for r in history)


def test_approve_does_not_mutate_confidence(env):
    tools_brain = _load_tools_brain()
    _seed_skill()

    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "regime-trend-rsi",
        "rationale": "try to bump confidence via metadata",
        "metadata_updates": {"confidence": "0.99"},
        "add_what_works": ["sneaky"],  # need at least one real change
    }))
    approval_id = out["approval_id"]

    from axiom.control_plane.approvals import post_approve_approval
    from axiom.control_plane.models import ApprovalDecisionBody

    post_approve_approval(
        approval_id,
        ApprovalDecisionBody(actor="operator", reason="ok", feedback=None),
    )

    refreshed = qs.read_skill("regime-trend-rsi")
    assert refreshed is not None
    # Confidence remains pinned to seed value (0.6) — Brain cannot bypass
    assert refreshed.confidence == pytest.approx(0.6)


def test_deny_skill_update_leaves_skill_untouched(env):
    tools_brain = _load_tools_brain()
    skill_before = _seed_skill()
    initial_version = skill_before.version

    out = json.loads(tools_brain._tool_propose_skill_update({
        "skill_name": "regime-trend-rsi",
        "rationale": "denied case",
        "add_what_works": ["should not appear"],
    }))
    approval_id = out["approval_id"]

    from axiom.control_plane.approvals import post_deny_approval
    from axiom.control_plane.models import ApprovalDecisionBody

    post_deny_approval(
        approval_id,
        ApprovalDecisionBody(actor="operator", reason="not now", feedback=None),
    )

    refreshed = qs.read_skill("regime-trend-rsi")
    assert refreshed is not None
    assert "should not appear" not in refreshed.what_works
    assert refreshed.version == initial_version
