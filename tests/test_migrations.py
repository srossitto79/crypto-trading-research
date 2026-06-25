"""H-D3: versioned migration system."""

from __future__ import annotations

import sqlite3

import pytest

from axiom.db import SCHEMA_VERSION, init_db, get_db
from axiom import migrations as mig


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


@pytest.fixture
def in_memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_ensure_migrations_table_creates_table(in_memory_conn):
    mig.ensure_migrations_table(in_memory_conn)
    rows = in_memory_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchall()
    assert rows, "schema_migrations table was not created"


def test_is_applied_false_for_unknown(in_memory_conn):
    assert mig.is_applied(in_memory_conn, "nonexistent_migration") is False


def test_record_applied_then_is_applied(in_memory_conn):
    mig.record_applied(in_memory_conn, "test_mig_001")
    assert mig.is_applied(in_memory_conn, "test_mig_001") is True


def test_record_applied_is_idempotent(in_memory_conn):
    mig.record_applied(in_memory_conn, "dup_mig")
    mig.record_applied(in_memory_conn, "dup_mig")
    rows = in_memory_conn.execute(
        "SELECT COUNT(*) AS c FROM schema_migrations WHERE name = ?", ("dup_mig",)
    ).fetchone()
    assert rows["c"] == 1


def test_apply_pending_runs_missing_and_records(in_memory_conn, monkeypatch):
    called: list[str] = []

    def _up_a(c):
        called.append("a")
        c.execute("CREATE TABLE mig_a_check (id INTEGER)")

    def _up_b(c):
        called.append("b")
        c.execute("CREATE TABLE mig_b_check (id INTEGER)")

    monkeypatch.setattr(
        mig, "MIGRATIONS",
        [mig.Migration("test_mig_a", _up_a), mig.Migration("test_mig_b", _up_b)],
    )

    applied = mig.apply_pending(in_memory_conn)
    assert applied == ["test_mig_a", "test_mig_b"]
    assert called == ["a", "b"]

    # Second call should apply nothing.
    called.clear()
    applied2 = mig.apply_pending(in_memory_conn)
    assert applied2 == []
    assert called == []


def test_apply_pending_stops_on_failure_and_does_not_record(in_memory_conn, monkeypatch):
    def _up_ok(c):
        c.execute("CREATE TABLE mig_ok (id INTEGER)")

    def _up_broken(c):
        raise RuntimeError("boom")

    def _up_never(c):
        c.execute("CREATE TABLE mig_never (id INTEGER)")

    monkeypatch.setattr(
        mig, "MIGRATIONS",
        [
            mig.Migration("ok_mig", _up_ok),
            mig.Migration("broken_mig", _up_broken),
            mig.Migration("never_mig", _up_never),
        ],
    )

    with pytest.raises(RuntimeError):
        mig.apply_pending(in_memory_conn)

    assert mig.is_applied(in_memory_conn, "ok_mig") is True
    assert mig.is_applied(in_memory_conn, "broken_mig") is False
    assert mig.is_applied(in_memory_conn, "never_mig") is False


def test_list_applied_returns_records_in_order(in_memory_conn):
    mig.record_applied(in_memory_conn, "mig_one")
    mig.record_applied(in_memory_conn, "mig_two")
    rows = mig.list_applied(in_memory_conn)
    names = [r["name"] for r in rows]
    assert "mig_one" in names and "mig_two" in names


def test_init_db_records_legacy_bulk_migration():
    """After init_db, the legacy bulk marker is in schema_migrations."""
    with get_db() as conn:
        assert mig.is_applied(conn, f"legacy_bulk_v{SCHEMA_VERSION}") is True


def test_portfolio_position_execution_type_migration_backfills(in_memory_conn):
    in_memory_conn.execute(
        """CREATE TABLE portfolio_positions (
            trade_id TEXT PRIMARY KEY, asset TEXT, direction TEXT, strategy TEXT,
            strategy_id TEXT, risk_pct REAL, entry_price REAL, correlation_group TEXT, opened_at TEXT
        )"""
    )
    in_memory_conn.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, execution_type TEXT)")
    in_memory_conn.execute("INSERT INTO trades (id, execution_type) VALUES ('t1', 'paper_challenger')")
    in_memory_conn.execute(
        "INSERT INTO portfolio_positions (trade_id, asset, direction, strategy, risk_pct) VALUES ('t1','BTC','long','s',0.01)"
    )
    # Position with no matching trade row -> stays NULL (legacy/live scope).
    in_memory_conn.execute(
        "INSERT INTO portfolio_positions (trade_id, asset, direction, strategy, risk_pct) VALUES ('t2','ETH','long','s',0.01)"
    )

    mig._m_2026_06_portfolio_position_execution_type(in_memory_conn)

    cols = {r[1] for r in in_memory_conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
    assert "execution_type" in cols
    assert in_memory_conn.execute(
        "SELECT execution_type FROM portfolio_positions WHERE trade_id='t1'"
    ).fetchone()["execution_type"] == "paper_challenger"
    assert in_memory_conn.execute(
        "SELECT execution_type FROM portfolio_positions WHERE trade_id='t2'"
    ).fetchone()["execution_type"] is None

    # Idempotent: re-running must not raise or change anything.
    mig._m_2026_06_portfolio_position_execution_type(in_memory_conn)
    assert in_memory_conn.execute(
        "SELECT execution_type FROM portfolio_positions WHERE trade_id='t1'"
    ).fetchone()["execution_type"] == "paper_challenger"


def test_live_max_concurrent_default_bump(in_memory_conn):
    import json

    in_memory_conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value JSON, updated_at TEXT)")

    # Legacy default of 1 -> bumped to 5; other keys preserved.
    in_memory_conn.execute(
        "INSERT INTO kv (key, value) VALUES ('Axiom:settings', ?)",
        (json.dumps({"max_concurrent_positions": 1, "foo": "bar"}),),
    )
    mig._m_2026_06_live_max_concurrent_default_bump(in_memory_conn)
    settings = json.loads(
        in_memory_conn.execute("SELECT value FROM kv WHERE key='Axiom:settings'").fetchone()["value"]
    )
    assert settings["max_concurrent_positions"] == 5
    assert settings["foo"] == "bar"


def test_live_max_concurrent_default_bump_preserves_deliberate_value(in_memory_conn):
    import json

    in_memory_conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value JSON, updated_at TEXT)")
    in_memory_conn.execute(
        "INSERT INTO kv (key, value) VALUES ('Axiom:settings', ?)",
        (json.dumps({"max_concurrent_positions": 3}),),
    )
    mig._m_2026_06_live_max_concurrent_default_bump(in_memory_conn)
    settings = json.loads(
        in_memory_conn.execute("SELECT value FROM kv WHERE key='Axiom:settings'").fetchone()["value"]
    )
    assert settings["max_concurrent_positions"] == 3


def test_live_max_concurrent_default_bump_no_settings_row(in_memory_conn):
    in_memory_conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value JSON, updated_at TEXT)")
    # No Axiom:settings row (fresh install) — must be a no-op, not an error.
    mig._m_2026_06_live_max_concurrent_default_bump(in_memory_conn)
    assert in_memory_conn.execute("SELECT COUNT(*) AS c FROM kv").fetchone()["c"] == 0


def test_direction_book_columns_migration(in_memory_conn):
    in_memory_conn.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, asset TEXT)")
    in_memory_conn.execute("CREATE TABLE portfolio_positions (trade_id TEXT PRIMARY KEY, asset TEXT)")

    mig._m_2026_06_direction_book_columns(in_memory_conn)

    for table in ("trades", "portfolio_positions"):
        cols = {r[1] for r in in_memory_conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "book" in cols, f"{table} missing book column"

    # Idempotent: re-running must not raise (column already exists).
    mig._m_2026_06_direction_book_columns(in_memory_conn)
