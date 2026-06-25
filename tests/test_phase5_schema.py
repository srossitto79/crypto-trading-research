"""Phase 5 / P5-T01 — schema migration v28 verification.

Confirms the Phase 5 schema bits exist after migration:
  * ``approvals.expires_at`` and the classifier columns
  * ``agent_toolset_overrides`` table + composite PK
  * ``brain_routines`` table

This test asserts ``SCHEMA_VERSION >= 28`` rather than ``== 28`` so that
later phases bumping the version (e.g. Phase 6+) do not silently break
the Phase 5 contract — the only thing this test is responsible for is
that the v28 migration ran.
"""
from __future__ import annotations


from axiom.db import SCHEMA_VERSION, get_db, init_db


PHASE5_APPROVAL_COLUMNS = (
    "expires_at",
    "classifier_recommendation",
    "classifier_reasoning",
    "classifier_model",
    "classifier_at",
    "auto_approved",
    "escalated_at",
    "escalated_to",
)


def test_schema_version_at_least_28() -> None:
    assert SCHEMA_VERSION >= 28


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_approvals_has_phase5_columns(AXIOM_db) -> None:
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "approvals")
    missing = [c for c in PHASE5_APPROVAL_COLUMNS if c not in cols]
    assert not missing, f"approvals table missing Phase 5 columns: {missing}"


def test_agent_toolset_overrides_table_exists(AXIOM_db) -> None:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_toolset_overrides'"
        ).fetchone()
        assert row is not None, "agent_toolset_overrides table not created"

        cols = _columns(conn, "agent_toolset_overrides")
        for required in ("agent_id", "context", "tool_name", "enabled", "updated_at"):
            assert required in cols, f"missing column {required!r}"


def test_agent_toolset_overrides_composite_pk(AXIOM_db) -> None:
    """PK is (agent_id, context, tool_name) — second insert with the same
    triple must fail (or be replaced via INSERT OR REPLACE)."""
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO agent_toolset_overrides "
            "(agent_id, context, tool_name, enabled, updated_at) "
            "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))",
            ("agent-x", "scheduled", "tool_y", 0),
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO agent_toolset_overrides "
                "(agent_id, context, tool_name, enabled, updated_at) "
                "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))",
                ("agent-x", "scheduled", "tool_y", 1),
            )
            conn.commit()
            raised = False
        except Exception:
            raised = True
            conn.rollback()
    assert raised, "duplicate (agent_id, context, tool_name) should violate PK"


def test_brain_routines_table_exists(AXIOM_db) -> None:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='brain_routines'"
        ).fetchone()
        assert row is not None, "brain_routines table not created"

        cols = _columns(conn, "brain_routines")
        for required in (
            "id",
            "name",
            "prompt",
            "cron_expr",
            "tools_context",
            "skills_json",
            "enabled",
            "created_by",
            "approval_id",
            "last_run_at",
            "last_status",
            "last_error",
            "created_at",
            "updated_at",
        ):
            assert required in cols, f"missing column {required!r}"


def test_brain_routines_name_unique(AXIOM_db) -> None:
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO brain_routines (name, prompt, cron_expr) VALUES (?, ?, ?)",
            ("daily-roundup", "summarize the day", "0 17 * * *"),
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO brain_routines (name, prompt, cron_expr) VALUES (?, ?, ?)",
                ("daily-roundup", "different prompt", "0 9 * * *"),
            )
            conn.commit()
            raised = False
        except Exception:
            raised = True
            conn.rollback()
    assert raised, "duplicate routine name should violate UNIQUE constraint"
