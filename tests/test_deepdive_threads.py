from axiom.db import get_db
from axiom.deepdive_db import (
    create_or_get_active_thread,
    archive_thread,
    get_thread,
)


def _seed_strategy(strategy_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, status, stage, source) "
            "VALUES (?, ?, 't', 'BTC', '1h', 'active', 'quick_screen', 'test')",
            (strategy_id, f"Strat {strategy_id}"),
        )


def test_create_first_thread_for_strategy(AXIOM_db):
    _seed_strategy("S00001")
    t = create_or_get_active_thread("S00001")
    assert t["strategy_id"] == "S00001"
    assert t["archived_at"] is None
    assert t["id"]


def test_get_existing_active_thread_idempotent(AXIOM_db):
    _seed_strategy("S00002")
    t1 = create_or_get_active_thread("S00002")
    t2 = create_or_get_active_thread("S00002")
    assert t1["id"] == t2["id"]


def test_archive_then_create_returns_fresh(AXIOM_db):
    _seed_strategy("S00003")
    t1 = create_or_get_active_thread("S00003")
    archive_thread(t1["id"])
    t2 = create_or_get_active_thread("S00003")
    assert t2["id"] != t1["id"]
    assert get_thread(t1["id"])["archived_at"] is not None


def test_get_thread_returns_none_for_unknown(AXIOM_db):
    assert get_thread("nonexistent") is None
