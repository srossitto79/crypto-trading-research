"""P3-T08 — /api/skills + /api/brain/lessons router tests."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from axiom.api import app
from axiom import quant_skills as qs
from axiom import skill_outcomes as so
from axiom.db import get_db


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


def _seed_skill(name: str, confidence: float = 0.6):
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
        what_doesnt_work=[],
        evidence=[{"recorded_at": "2026-04-25T00:00:00+00:00", "sharpe": 1.0}],
    )
    qs.write_skill(skill)
    return skill


def _seed_task_with_citations(strategy_id: str, skills: list[str]) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO agent_tasks (agent_id, type, strategy_id, output_data, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("brain", "ideation", strategy_id, json.dumps({"cited_skills": skills}), "completed"),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# /api/skills                                                                 #
# --------------------------------------------------------------------------- #


def test_list_skills_returns_metadata_only(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")
    _seed_skill("regime-trend-macd")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    for item in body["items"]:
        assert "what_works" not in item
        assert "evidence" not in item
        assert "version" in item
        assert "confidence" in item


def test_get_skill_returns_full_detail(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")

    r = client.get("/api/skills/regime-trend-rsi")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "regime-trend-rsi"
    assert "what_works" in body
    assert "evidence" in body


def test_get_skill_404(env):
    client = TestClient(app)
    r = client.get("/api/skills/nonexistent")
    assert r.status_code == 404


def test_get_skill_section(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")

    r = client.get("/api/skills/regime-trend-rsi/section/what_works")
    assert r.status_code == 200
    assert r.json()["what_works"] == ["alpha"]


def test_get_skill_section_unknown(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")
    r = client.get("/api/skills/regime-trend-rsi/section/bogus")
    assert r.status_code == 400


def test_skill_history(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")
    qs.update_skill(
        "regime-trend-rsi",
        new_evidence={"recorded_at": "2026-04-26T00:00:00+00:00", "sharpe": 1.1},
        change_summary="bumped",
    )

    r = client.get("/api/skills/regime-trend-rsi/history")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 2


def test_skill_diff(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")
    qs.update_skill(
        "regime-trend-rsi",
        new_evidence={"recorded_at": "2026-04-26T00:00:00+00:00", "sharpe": 1.1},
        change_summary="bump",
    )

    r = client.get("/api/skills/regime-trend-rsi/diff?from_version=1&to_version=2")
    assert r.status_code == 200
    body = r.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 2
    assert isinstance(body["diff"], str)


def test_declining_skills_widget(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi", confidence=0.6)
    _seed_skill("regime-trend-macd", confidence=0.6)
    _seed_task_with_citations("s-A", ["regime-trend-rsi"])
    _seed_task_with_citations("s-B", ["regime-trend-macd"])

    so.record_outcome("s-A", "negative", "transition_stage:archived")
    so.record_outcome("s-B", "positive", "transition_stage:live_graduated")

    r = client.get("/api/skills/declining?days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 30
    names = {item["skill_name"] for item in body["items"]}
    assert "regime-trend-rsi" in names
    assert "regime-trend-macd" not in names  # positive — not declining


def test_skill_outcomes_endpoint(env):
    client = TestClient(app)
    _seed_skill("regime-trend-rsi")
    _seed_task_with_citations("s-A", ["regime-trend-rsi"])
    so.record_outcome("s-A", "negative", "transition_stage:archived")

    r = client.get("/api/skills/regime-trend-rsi/outcomes")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["outcome"] == "negative"


# --------------------------------------------------------------------------- #
# /api/brain/lessons                                                          #
# --------------------------------------------------------------------------- #


def test_create_and_list_lessons(env):
    client = TestClient(app)

    create = client.post(
        "/api/brain/lessons",
        json={
            "situation_pattern": "TRENDING with low volume",
            "lesson_text": "Skip breakout strategies",
            "evidence_decisions": [1, 2],
            "confidence": 0.7,
        },
    )
    assert create.status_code == 200
    body = create.json()
    assert body["id"] > 0
    assert body["situation_pattern"] == "TRENDING with low volume"
    assert body["evidence_decisions"] == [1, 2]

    listed = client.get("/api/brain/lessons")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


def test_create_lesson_validation_error(env):
    client = TestClient(app)
    r = client.post(
        "/api/brain/lessons",
        json={"situation_pattern": "", "lesson_text": "x", "evidence_decisions": []},
    )
    assert r.status_code == 422


def test_get_update_delete_lesson(env):
    client = TestClient(app)
    created = client.post(
        "/api/brain/lessons",
        json={
            "situation_pattern": "p",
            "lesson_text": "l",
            "evidence_decisions": [1],
            "confidence": 0.5,
        },
    ).json()
    lesson_id = created["id"]

    got = client.get(f"/api/brain/lessons/{lesson_id}")
    assert got.status_code == 200

    updated = client.put(
        f"/api/brain/lessons/{lesson_id}",
        json={"lesson_text": "revised"},
    )
    assert updated.status_code == 200
    assert updated.json()["lesson_text"] == "revised"

    validated = client.post(f"/api/brain/lessons/{lesson_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["last_validated_at"] is not None

    deleted = client.delete(f"/api/brain/lessons/{lesson_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/brain/lessons/{lesson_id}").status_code == 404


def test_search_lessons(env):
    client = TestClient(app)
    client.post(
        "/api/brain/lessons",
        json={
            "situation_pattern": "TRENDING regime",
            "lesson_text": "Use breakout",
            "evidence_decisions": [],
        },
    )
    client.post(
        "/api/brain/lessons",
        json={
            "situation_pattern": "RANGE_BOUND regime",
            "lesson_text": "Mean reversion",
            "evidence_decisions": [],
        },
    )

    r = client.get("/api/brain/lessons/search?q=TRENDING")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert "TRENDING" in body["items"][0]["situation_pattern"]
