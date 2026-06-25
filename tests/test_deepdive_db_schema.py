"""Schema tests for the Deepdive Strategy Chat tables."""

from axiom.db import get_db


def test_deepdive_threads_table_exists(AXIOM_db):
    with get_db() as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("deepdive_threads",),
        )
        assert cur.fetchone() is not None


def test_deepdive_messages_table_exists(AXIOM_db):
    with get_db() as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("deepdive_messages",),
        )
        assert cur.fetchone() is not None


def test_deepdive_threads_columns(AXIOM_db):
    with get_db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(deepdive_threads)")}
    assert cols == {"id", "strategy_id", "created_at", "updated_at", "archived_at"}


def test_deepdive_messages_columns(AXIOM_db):
    with get_db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(deepdive_messages)")}
    assert cols == {
        "id",
        "thread_id",
        "role",
        "content",
        "tool_call_json",
        "created_at",
        "cost_usd",
        "model",
    }


def test_deepdive_indices_exist(AXIOM_db):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?, ?)",
            ("idx_deepdive_threads_strategy_active", "idx_deepdive_messages_thread"),
        ).fetchall()
    names = {r[0] for r in rows}
    assert names == {"idx_deepdive_threads_strategy_active", "idx_deepdive_messages_thread"}
