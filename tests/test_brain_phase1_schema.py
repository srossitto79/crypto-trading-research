"""Phase 1 schema migration tests — brain_memory, brain_decisions, FTS5.

Asserts the Phase 1 (P1-T01) DDL applies cleanly on a fresh DB and is a
no-op on a re-run, and that the single-row CHECK on brain_memory holds.
"""
from __future__ import annotations

import sqlite3
import tempfile

import pytest

from axiom import db as AXIOM_db


@pytest.fixture
def fresh_db(monkeypatch):
    """Build a one-off DB by routing AXIOM_HOME at a tmpdir."""
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("AXIOM_HOME", td)
        # axiom.db caches paths via module-level globals; reach in to reset.
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


def test_brain_memory_table_present(fresh_db):
    assert _table_exists(fresh_db, "brain_memory")
    row = fresh_db.execute("SELECT id, body, updated_by FROM brain_memory").fetchone()
    assert row is not None
    assert row["id"] == 1
    assert row["body"] == ""
    assert row["updated_by"] == "migration"


def test_brain_memory_single_row_invariant(fresh_db):
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO brain_memory (id, body, updated_by) VALUES (2, 'second', 'test')"
        )


def test_brain_memory_history_table_present(fresh_db):
    assert _table_exists(fresh_db, "brain_memory_history")
    cols = {row["name"] for row in fresh_db.execute("PRAGMA table_info(brain_memory_history)")}
    assert {"mutation_type", "before_excerpt", "after_excerpt", "mutated_at", "mutated_by"} <= cols


def test_brain_decisions_table_present(fresh_db):
    assert _table_exists(fresh_db, "brain_decisions")
    cols = {row["name"] for row in fresh_db.execute("PRAGMA table_info(brain_decisions)")}
    assert {
        "cycle_id",
        "situation_summary",
        "decision_json",
        "action_taken",
        "outcome_observed",
        "outcome_at",
        "prompt_hash",
    } <= cols


def test_brain_decisions_indices_present(fresh_db):
    indices = {
        row["name"]
        for row in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='brain_decisions'"
        )
    }
    assert "idx_brain_decisions_cycle" in indices
    assert "idx_brain_decisions_prompt_hash" in indices
    assert "idx_brain_decisions_outcome" in indices


def test_brain_decisions_fts_roundtrip(fresh_db):
    fresh_db.execute(
        "INSERT INTO brain_decisions (cycle_id, situation_summary, action_taken) "
        "VALUES (?, ?, ?)",
        ("c-1", "BTC breakout above 70k with rising volume", "promoted strategy s-42"),
    )
    fresh_db.commit()
    rows = fresh_db.execute(
        "SELECT rowid FROM brain_decisions_fts WHERE brain_decisions_fts MATCH 'breakout'"
    ).fetchall()
    assert len(rows) == 1


def test_brain_decisions_fts_update_then_match(fresh_db):
    cur = fresh_db.execute(
        "INSERT INTO brain_decisions (situation_summary) VALUES ('initial text')"
    )
    rowid = cur.lastrowid
    fresh_db.execute(
        "UPDATE brain_decisions SET situation_summary=? WHERE id=?",
        ("updated content with uniquetokenxyz", rowid),
    )
    fresh_db.commit()
    rows = fresh_db.execute(
        "SELECT rowid FROM brain_decisions_fts WHERE brain_decisions_fts MATCH 'uniquetokenxyz'"
    ).fetchall()
    assert len(rows) == 1
    rows_old = fresh_db.execute(
        "SELECT rowid FROM brain_decisions_fts WHERE brain_decisions_fts MATCH 'initial'"
    ).fetchall()
    assert len(rows_old) == 0


def test_brain_decisions_fts_delete_unindexes(fresh_db):
    cur = fresh_db.execute(
        "INSERT INTO brain_decisions (situation_summary) VALUES ('tobedeletedmarker')"
    )
    rowid = cur.lastrowid
    fresh_db.execute("DELETE FROM brain_decisions WHERE id=?", (rowid,))
    fresh_db.commit()
    rows = fresh_db.execute(
        "SELECT rowid FROM brain_decisions_fts "
        "WHERE brain_decisions_fts MATCH 'tobedeletedmarker'"
    ).fetchall()
    assert len(rows) == 0


def test_agent_tasks_fts_present(fresh_db):
    assert _table_exists(fresh_db, "agent_tasks_fts")


def test_task_audit_log_fts_present(fresh_db):
    assert _table_exists(fresh_db, "task_audit_log_fts")


def test_agent_tasks_brain_decision_id_column_present(fresh_db):
    cols = {row["name"] for row in fresh_db.execute("PRAGMA table_info(agent_tasks)")}
    assert "brain_decision_id" in cols


def test_schema_version_is_at_least_24(fresh_db):
    row = fresh_db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row["v"] >= 24


def test_migration_idempotent(fresh_db):
    """Re-running init_db on an already-migrated DB must not raise."""
    if hasattr(AXIOM_db, "_init_db_done"):
        AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
    AXIOM_db.init_db()
    row = fresh_db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row["v"] >= 24
