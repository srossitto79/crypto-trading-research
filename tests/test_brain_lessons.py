"""P3-T06 — brain_lessons CRUD + FTS5 search."""
from __future__ import annotations

import tempfile

import pytest

from axiom import db as AXIOM_db
from axiom import brain_lessons as bl


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_dir = tempfile.mkdtemp()
    monkeypatch.setenv("AXIOM_HOME", db_dir)
    if hasattr(AXIOM_db, "_DB_PATH"):
        AXIOM_db._DB_PATH = None  # type: ignore[attr-defined]
    if hasattr(AXIOM_db, "_init_db_done"):
        AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
    AXIOM_db.init_db()
    yield {"db_dir": db_dir}


def test_create_and_get_lesson(env):
    row = bl.create_lesson(
        situation_pattern="HighVolatility regime + low liquidity",
        lesson_text="Skip mean-reversion strategies when ADX > 30",
        evidence_decisions=[101, 102],
        confidence=0.7,
    )
    assert row["id"] > 0
    assert row["situation_pattern"] == "HighVolatility regime + low liquidity"
    assert row["lesson_text"] == "Skip mean-reversion strategies when ADX > 30"
    assert row["evidence_decisions"] == [101, 102]
    assert row["confidence"] == pytest.approx(0.7)
    assert row["created_by"] == "brain"
    assert row["last_validated_at"] is None
    assert row["created_at"] is not None

    fetched = bl.get_lesson(row["id"])
    assert fetched is not None
    assert fetched["id"] == row["id"]
    assert fetched["evidence_decisions"] == [101, 102]


def test_get_lesson_nonexistent(env):
    assert bl.get_lesson(999) is None


def test_list_lessons_orders_and_filters(env):
    bl.create_lesson("pat A", "lesson A", [1], confidence=0.3)
    bl.create_lesson("pat B", "lesson B", [2], confidence=0.6)
    bl.create_lesson("pat C", "lesson C", [3], confidence=0.9)

    all_rows = bl.list_lessons()
    assert len(all_rows) == 3

    # min_confidence filter
    high_conf = bl.list_lessons(min_confidence=0.5)
    assert len(high_conf) == 2
    assert all(r["confidence"] >= 0.5 for r in high_conf)

    # limit
    limited = bl.list_lessons(limit=1)
    assert len(limited) == 1


def test_update_lesson_partial(env):
    row = bl.create_lesson("pat", "original lesson", [1], confidence=0.5)

    updated = bl.update_lesson(row["id"], lesson_text="revised lesson")
    assert updated is not None
    assert updated["lesson_text"] == "revised lesson"
    assert updated["situation_pattern"] == "pat"  # untouched
    assert updated["confidence"] == pytest.approx(0.5)

    bumped = bl.update_lesson(row["id"], confidence=0.85)
    assert bumped["confidence"] == pytest.approx(0.85)
    assert bumped["lesson_text"] == "revised lesson"  # preserved


def test_update_lesson_no_args_returns_current(env):
    row = bl.create_lesson("pat", "lesson", [1])
    same = bl.update_lesson(row["id"])
    assert same is not None
    assert same["id"] == row["id"]


def test_update_lesson_nonexistent_returns_none(env):
    assert bl.update_lesson(999, lesson_text="x") is None


def test_delete_lesson(env):
    row = bl.create_lesson("pat", "lesson", [1])
    assert bl.delete_lesson(row["id"]) is True
    assert bl.get_lesson(row["id"]) is None
    # second delete is idempotent (returns False)
    assert bl.delete_lesson(row["id"]) is False


def test_search_lessons_by_situation_pattern(env):
    bl.create_lesson(
        "TRENDING regime with low ADX",
        "Avoid breakout strategies",
        [1],
        confidence=0.6,
    )
    bl.create_lesson(
        "RANGE_BOUND regime with high RSI divergence",
        "Mean-reversion plays well",
        [2],
        confidence=0.8,
    )

    hits = bl.search_lessons("TRENDING")
    assert len(hits) == 1
    assert "TRENDING" in hits[0]["situation_pattern"]


def test_search_lessons_by_lesson_text(env):
    bl.create_lesson("regime A", "Avoid breakout strategies on low volume", [1])
    bl.create_lesson("regime B", "Use mean-reversion when RSI < 30", [2])

    hits = bl.search_lessons("breakout")
    assert len(hits) == 1
    assert "breakout" in hits[0]["lesson_text"]

    hits2 = bl.search_lessons("RSI")
    assert len(hits2) == 1
    assert "RSI" in hits2[0]["lesson_text"]


def test_search_lessons_orders_by_confidence_desc(env):
    bl.create_lesson("alpha pattern", "alpha lesson", [1], confidence=0.4)
    bl.create_lesson("alpha pattern", "alpha lesson", [2], confidence=0.9)
    bl.create_lesson("alpha pattern", "alpha lesson", [3], confidence=0.6)

    hits = bl.search_lessons("alpha")
    assert len(hits) == 3
    confidences = [h["confidence"] for h in hits]
    assert confidences == sorted(confidences, reverse=True)


def test_search_lessons_empty_query(env):
    bl.create_lesson("pat", "lesson", [1])
    assert bl.search_lessons("") == []
    assert bl.search_lessons("   ") == []


def test_record_brain_lesson_returns_id(env):
    lesson_id = bl.record_brain_lesson(
        "pattern X",
        "lesson X",
        evidence_decisions=[1, 2, 3],
        confidence=0.55,
    )
    assert isinstance(lesson_id, int)
    assert lesson_id > 0

    row = bl.get_lesson(lesson_id)
    assert row is not None
    assert row["situation_pattern"] == "pattern X"
    assert row["evidence_decisions"] == [1, 2, 3]
    assert row["confidence"] == pytest.approx(0.55)


def test_mark_validated_sets_timestamp(env):
    row = bl.create_lesson("pat", "lesson", [1])
    assert row["last_validated_at"] is None

    updated = bl.mark_validated(row["id"])
    assert updated is not None
    assert updated["last_validated_at"] is not None
    # ISO-ish format with timezone suffix
    assert "T" in updated["last_validated_at"]
    assert "+00:00" in updated["last_validated_at"]


def test_create_validates_empty_situation_pattern(env):
    with pytest.raises(ValueError, match="situation_pattern"):
        bl.create_lesson("", "lesson", [1])
    with pytest.raises(ValueError, match="situation_pattern"):
        bl.create_lesson("   ", "lesson", [1])


def test_create_validates_empty_lesson_text(env):
    with pytest.raises(ValueError, match="lesson_text"):
        bl.create_lesson("pat", "", [1])
    with pytest.raises(ValueError, match="lesson_text"):
        bl.create_lesson("pat", "   ", [1])


def test_create_validates_evidence_decisions_type(env):
    with pytest.raises(ValueError, match="evidence_decisions"):
        bl.create_lesson("pat", "lesson", "not-a-list")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="evidence_decisions"):
        bl.create_lesson("pat", "lesson", [1, "two", 3])  # type: ignore[list-item]


def test_create_validates_confidence_range(env):
    with pytest.raises(ValueError, match="confidence"):
        bl.create_lesson("pat", "lesson", [1], confidence=-0.1)
    with pytest.raises(ValueError, match="confidence"):
        bl.create_lesson("pat", "lesson", [1], confidence=1.5)


def test_update_validates_confidence_range(env):
    row = bl.create_lesson("pat", "lesson", [1])
    with pytest.raises(ValueError, match="confidence"):
        bl.update_lesson(row["id"], confidence=2.0)


def test_fts5_reflects_updates(env):
    row = bl.create_lesson("first pattern", "first lesson", [1])
    # initial search by old text
    assert len(bl.search_lessons("first")) == 1

    bl.update_lesson(row["id"], situation_pattern="totally renamed pattern", lesson_text="totally renamed lesson")
    # FTS triggers should reflect the rename
    assert bl.search_lessons("first") == []
    assert len(bl.search_lessons("renamed")) == 1


def test_fts5_reflects_deletes(env):
    row = bl.create_lesson("deletable pattern", "deletable lesson", [1])
    assert len(bl.search_lessons("deletable")) == 1
    bl.delete_lesson(row["id"])
    assert bl.search_lessons("deletable") == []
