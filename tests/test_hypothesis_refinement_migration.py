"""Migration test for the hypothesis refinement loop schema changes.

Phase 0 of docs/plans/2026-04-17-hypothesis-refinement-loop-plan.md.

Verifies that the named migration `2026_04_hypothesis_refinement_loop`:
  * Adds the four new hypotheses columns
  * Adds the two new strategies columns
  * Creates the supporting indexes
  * Seeds HYP-LEGACY in archived state
  * Backfills orphan strategies (NULL hypothesis_id) into HYP-LEGACY
  * Is idempotent (second invocation is a no-op)
"""

from __future__ import annotations

import sqlite3

from axiom.db import get_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def test_migration_adds_hypothesis_columns(AXIOM_db) -> None:
    with get_db() as conn:
        cols = _columns(conn, "hypotheses")
    assert "graduated_at" in cols
    assert "next_revisit_at" in cols
    assert "last_revisited_at" in cols
    assert "revisit_count" in cols


def test_migration_adds_strategy_columns(AXIOM_db) -> None:
    with get_db() as conn:
        cols = _columns(conn, "strategies")
    assert "parent_strategy_id" in cols
    assert "canonical" in cols


def test_migration_creates_indexes(AXIOM_db) -> None:
    with get_db() as conn:
        strat_idx = _indexes(conn, "strategies")
        hyp_idx = _indexes(conn, "hypotheses")
    assert "idx_strategies_parent_strategy_id" in strat_idx
    assert "idx_strategies_canonical" in strat_idx
    assert "idx_hypotheses_manager_state_status" in hyp_idx
    assert "idx_hypotheses_next_revisit_at" in hyp_idx


def test_hyp_legacy_seeded_archived(AXIOM_db) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, display_id, manager_state, status, source_type "
            "FROM hypotheses WHERE id = 'HYP-LEGACY'"
        ).fetchone()
    assert row is not None
    assert row["display_id"] == "H000"
    assert row["manager_state"] == "archived"
    assert row["status"] == "disproven"
    assert row["source_type"] == "system_backfill"


def test_orphan_strategies_backfilled_to_legacy(AXIOM_db) -> None:
    """Strategies created with hypothesis_id NULL get re-pointed to HYP-LEGACY."""
    with get_db() as conn:
        # Insert two orphan strategies (post-init, simulating pre-migration data
        # — the NULL backfill in the migration already ran on init, so we
        # insert orphans now and re-run the named migration to verify
        # idempotent behaviour.
        from axiom.db import _now
        now = _now()
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, status, stage, owner, hypothesis_id, base_id, display_id, last_prefix, audit_summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '{}', 'quick_screen', 'quick_screen', NULL, NULL, ?, ?, 'S', '[]', ?, ?)",
            ("S99001", "orphan-1", "test", "BTC-PERP", "1h", 99001, "S99001", now, now),
        )
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, status, stage, owner, hypothesis_id, base_id, display_id, last_prefix, audit_summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '{}', 'quick_screen', 'quick_screen', NULL, NULL, ?, ?, 'S', '[]', ?, ?)",
            ("S99002", "orphan-2", "test", "ETH-PERP", "1h", 99002, "S99002", now, now),
        )
        conn.commit()

    # Re-run the migration explicitly (simulating an operator forcing a re-apply
    # via a raw call). Should be safe because UPDATE ... WHERE NULL is idempotent.
    from axiom.migrations import _m_2026_04_hypothesis_refinement_loop
    with get_db() as conn:
        _m_2026_04_hypothesis_refinement_loop(conn)
        conn.commit()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, hypothesis_id FROM strategies WHERE id IN ('S99001', 'S99002')"
        ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["hypothesis_id"] == "HYP-LEGACY"


def test_migration_is_idempotent_when_re_applied(AXIOM_db) -> None:
    """Calling the migration function a second time must not raise or duplicate rows."""
    from axiom.migrations import _m_2026_04_hypothesis_refinement_loop
    with get_db() as conn:
        _m_2026_04_hypothesis_refinement_loop(conn)
        _m_2026_04_hypothesis_refinement_loop(conn)
        conn.commit()
        # Exactly one HYP-LEGACY
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM hypotheses WHERE id = 'HYP-LEGACY'"
        ).fetchone()["n"]
    assert count == 1


def test_migration_recorded_in_schema_migrations(AXIOM_db) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM schema_migrations WHERE name = '2026_04_hypothesis_refinement_loop'"
        ).fetchone()
    assert row is not None


def test_revisit_count_defaults_to_zero(AXIOM_db) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT revisit_count FROM hypotheses WHERE id = 'HYP-LEGACY'"
        ).fetchone()
    assert row["revisit_count"] == 0


def test_canonical_defaults_to_zero_for_new_strategies(AXIOM_db) -> None:
    """New strategies get canonical=0 by default."""
    from axiom.db import _now
    now = _now()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, status, stage, owner, hypothesis_id, base_id, display_id, last_prefix, audit_summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '{}', 'quick_screen', 'quick_screen', NULL, ?, ?, ?, 'S', '[]', ?, ?)",
            ("S99100", "default-canonical-test", "test", "BTC-PERP", "1h", "HYP-LEGACY", 99100, "S99100", now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT canonical, parent_strategy_id FROM strategies WHERE id = 'S99100'"
        ).fetchone()
    assert row["canonical"] == 0
    assert row["parent_strategy_id"] is None
