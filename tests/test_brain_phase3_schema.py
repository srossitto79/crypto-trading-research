"""Phase 3 schema migration tests — quant_skills_history, skill_outcome_events,
brain_lessons, brain_lessons_fts.

Verifies the Phase 3 (P3-T01) DDL applies cleanly on a fresh DB, indexes are
present, CHECK constraints fire, FTS5 mirror keeps in sync, and the migration
is idempotent on re-run.
"""
from __future__ import annotations

import sqlite3
import tempfile

import pytest

from axiom import db as AXIOM_db


@pytest.fixture
def fresh_db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("AXIOM_HOME", td)
        if hasattr(AXIOM_db, "_DB_PATH"):
            AXIOM_db._DB_PATH = None  # type: ignore[attr-defined]
        if hasattr(AXIOM_db, "_init_db_done"):
            AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
        AXIOM_db.init_db()
        with AXIOM_db.get_db() as conn:
            yield conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


# ── quant_skills_history ────────────────────────────────────────────────────


def test_quant_skills_history_table_present(fresh_db):
    assert _table_exists(fresh_db, "quant_skills_history")
    cols = {row["name"] for row in fresh_db.execute("PRAGMA table_info(quant_skills_history)")}
    assert {
        "skill_name",
        "version",
        "parent_version",
        "body_diff",
        "change_summary",
        "evidence_task_id",
        "created_by",
        "created_at",
    } <= cols


def test_quant_skills_history_indexes_present(fresh_db):
    assert _index_exists(fresh_db, "idx_quant_skills_history_skill_version")
    assert _index_exists(fresh_db, "idx_quant_skills_history_evidence_task")
    assert _index_exists(fresh_db, "idx_quant_skills_history_created_at")


def test_quant_skills_history_unique_skill_version(fresh_db):
    fresh_db.execute(
        "INSERT INTO quant_skills_history (skill_name, version, body_diff) "
        "VALUES ('foo', 1, '')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO quant_skills_history (skill_name, version, body_diff) "
            "VALUES ('foo', 1, 'dup')"
        )


# ── skill_outcome_events ────────────────────────────────────────────────────


def test_skill_outcome_events_table_present(fresh_db):
    assert _table_exists(fresh_db, "skill_outcome_events")
    cols = {row["name"] for row in fresh_db.execute("PRAGMA table_info(skill_outcome_events)")}
    assert {
        "skill_name",
        "strategy_id",
        "outcome",
        "confidence_delta",
        "confidence_before",
        "confidence_after",
        "evidence_task_id",
        "triggered_by",
        "notes",
    } <= cols


def test_skill_outcome_events_outcome_check(fresh_db):
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO skill_outcome_events "
            "(skill_name, strategy_id, outcome, confidence_delta, confidence_before, confidence_after, triggered_by) "
            "VALUES ('foo', 's-1', 'invalid', 0, 0.5, 0.5, 'test')"
        )


def test_skill_outcome_events_idempotent_unique(fresh_db):
    fresh_db.execute(
        "INSERT INTO skill_outcome_events "
        "(skill_name, strategy_id, outcome, confidence_delta, confidence_before, confidence_after, triggered_by) "
        "VALUES ('foo', 's-1', 'negative', -0.05, 0.5, 0.45, 'transition_stage:archived')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO skill_outcome_events "
            "(skill_name, strategy_id, outcome, confidence_delta, confidence_before, confidence_after, triggered_by) "
            "VALUES ('foo', 's-1', 'negative', -0.05, 0.45, 0.40, 'transition_stage:archived')"
        )


# ── brain_lessons ───────────────────────────────────────────────────────────


def test_brain_lessons_table_present(fresh_db):
    assert _table_exists(fresh_db, "brain_lessons")
    cols = {row["name"] for row in fresh_db.execute("PRAGMA table_info(brain_lessons)")}
    assert {
        "situation_pattern",
        "lesson_text",
        "evidence_decisions_json",
        "confidence",
        "created_at",
        "last_validated_at",
        "created_by",
    } <= cols


def test_brain_lessons_confidence_check_too_high(fresh_db):
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO brain_lessons (situation_pattern, lesson_text, confidence) "
            "VALUES ('p', 'l', 1.5)"
        )


def test_brain_lessons_confidence_check_negative(fresh_db):
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO brain_lessons (situation_pattern, lesson_text, confidence) "
            "VALUES ('p', 'l', -0.1)"
        )


def test_brain_lessons_fts_roundtrip(fresh_db):
    fresh_db.execute(
        "INSERT INTO brain_lessons (situation_pattern, lesson_text) "
        "VALUES ('trend on RANGE_BOUND regime', 'do not promote uniquemarker on choppy')"
    )
    fresh_db.commit()
    rows = fresh_db.execute(
        "SELECT rowid FROM brain_lessons_fts WHERE brain_lessons_fts MATCH 'uniquemarker'"
    ).fetchall()
    assert len(rows) == 1


def test_brain_lessons_fts_update_reflects(fresh_db):
    cur = fresh_db.execute(
        "INSERT INTO brain_lessons (situation_pattern, lesson_text) "
        "VALUES ('initial pattern', 'firsttoken text')"
    )
    rowid = cur.lastrowid
    fresh_db.execute(
        "UPDATE brain_lessons SET lesson_text=? WHERE id=?",
        ("secondtoken text", rowid),
    )
    fresh_db.commit()
    found_old = fresh_db.execute(
        "SELECT rowid FROM brain_lessons_fts WHERE brain_lessons_fts MATCH 'firsttoken'"
    ).fetchall()
    assert len(found_old) == 0
    found_new = fresh_db.execute(
        "SELECT rowid FROM brain_lessons_fts WHERE brain_lessons_fts MATCH 'secondtoken'"
    ).fetchall()
    assert len(found_new) == 1


def test_brain_lessons_fts_delete_unindexes(fresh_db):
    cur = fresh_db.execute(
        "INSERT INTO brain_lessons (situation_pattern, lesson_text) "
        "VALUES ('p', 'aboutomarker disappear')"
    )
    rowid = cur.lastrowid
    fresh_db.execute("DELETE FROM brain_lessons WHERE id=?", (rowid,))
    fresh_db.commit()
    rows = fresh_db.execute(
        "SELECT rowid FROM brain_lessons_fts WHERE brain_lessons_fts MATCH 'aboutomarker'"
    ).fetchall()
    assert len(rows) == 0


# ── meta ────────────────────────────────────────────────────────────────────


def test_schema_version_is_at_least_26(fresh_db):
    row = fresh_db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row["v"] >= 26


def test_phase3_migration_idempotent(fresh_db):
    if hasattr(AXIOM_db, "_init_db_done"):
        AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
    AXIOM_db.init_db()
    assert _table_exists(fresh_db, "quant_skills_history")
    assert _table_exists(fresh_db, "skill_outcome_events")
    assert _table_exists(fresh_db, "brain_lessons")
    assert _table_exists(fresh_db, "brain_lessons_fts")
