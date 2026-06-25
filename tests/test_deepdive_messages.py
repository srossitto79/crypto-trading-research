from axiom.db import get_db
from axiom.deepdive_db import (
    create_or_get_active_thread,
    append_message,
    list_messages,
)


def _seed_strategy(strategy_id: str) -> None:
    """Insert minimal strategies row to satisfy the FK."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO strategies (id, name) VALUES (?, ?)",
            (strategy_id, f"test {strategy_id}"),
        )
        conn.commit()


def test_append_user_message(AXIOM_db):
    _seed_strategy("S10001")
    t = create_or_get_active_thread("S10001")
    m = append_message(t["id"], role="user", content="hello")
    assert m["role"] == "user"
    assert m["content"] == "hello"
    assert m["thread_id"] == t["id"]


def test_list_messages_in_order(AXIOM_db):
    _seed_strategy("S10002")
    t = create_or_get_active_thread("S10002")
    append_message(t["id"], role="user", content="first")
    append_message(t["id"], role="assistant", content="second")
    msgs = list_messages(t["id"])
    assert [m["content"] for m in msgs] == ["first", "second"]


def test_append_tool_message_persists_json(AXIOM_db):
    _seed_strategy("S10003")
    t = create_or_get_active_thread("S10003")
    m = append_message(
        t["id"], role="tool", content="ok",
        tool_call={"name": "run_backtest", "args": {"timeframe": "1h"}},
        cost_usd=0.0123, model="claude-sonnet-4-6",
    )
    assert m["tool_call"]["name"] == "run_backtest"
    assert m["cost_usd"] == 0.0123
    assert m["model"] == "claude-sonnet-4-6"


def test_thread_cost_total_empty_and_populated(AXIOM_db):
    from axiom.deepdive_db import thread_cost_total
    _seed_strategy("S10004")
    t = create_or_get_active_thread("S10004")
    assert thread_cost_total(t["id"]) == 0.0
    append_message(t["id"], role="assistant", content="hi", cost_usd=0.01)
    append_message(t["id"], role="assistant", content="bye", cost_usd=0.02)
    assert round(thread_cost_total(t["id"]), 4) == 0.03
