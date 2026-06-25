"""Phase 1 brain_memory module tests (P1-T02).

Covers cap enforcement, history excerpts, missing-needle no-ops, history
ordering, and concurrent add_memory safety.
"""
from __future__ import annotations

import tempfile
import threading

import pytest

from axiom import brain_memory as bm
from axiom import db as AXIOM_db


@pytest.fixture
def fresh_home(monkeypatch):
    """Route AXIOM_HOME at a tmpdir and reset cached DB module state."""
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("AXIOM_HOME", td)
        if hasattr(AXIOM_db, "_DB_PATH"):
            AXIOM_db._DB_PATH = None  # type: ignore[attr-defined]
        if hasattr(AXIOM_db, "_init_db_done"):
            AXIOM_db._init_db_done = False  # type: ignore[attr-defined]
        AXIOM_db.init_db()
        yield td


def _history_count() -> int:
    with AXIOM_db.get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM brain_memory_history").fetchone()[0]


def test_initial_state_is_empty(fresh_home):
    meta = bm.get_memory_with_meta()
    assert meta["body"] == ""
    assert meta["char_count"] == 0
    assert meta["cap"] == bm.MAX_MEMORY_CHARS == 2000
    assert bm.get_memory() == ""


def test_set_memory_writes_history_row(fresh_home):
    out = bm.set_memory("hello world", mutated_by="brain")
    assert out["ok"] is True
    assert out["char_count"] == len("hello world")
    assert bm.get_memory() == "hello world"
    history = bm.list_history()
    assert len(history) == 1
    assert history[0]["mutation_type"] == "replace"
    assert history[0]["before_excerpt"] == ""
    assert history[0]["after_excerpt"] == "hello world"
    assert history[0]["mutated_by"] == "brain"


def test_set_memory_at_cap_succeeds(fresh_home):
    body = "x" * bm.MAX_MEMORY_CHARS
    bm.set_memory(body, mutated_by="brain")
    assert bm.get_memory() == body


def test_set_memory_over_cap_raises(fresh_home):
    bm.set_memory("seeded", mutated_by="brain")
    seeded_history = _history_count()
    with pytest.raises(bm.BrainMemoryTooLargeError) as exc:
        bm.set_memory("x" * (bm.MAX_MEMORY_CHARS + 1), mutated_by="brain")
    assert exc.value.cap == bm.MAX_MEMORY_CHARS
    assert exc.value.attempted_len == bm.MAX_MEMORY_CHARS + 1
    assert exc.value.current_len == len("seeded")
    # Body must be unchanged and no new history row written.
    assert bm.get_memory() == "seeded"
    assert _history_count() == seeded_history


def test_add_memory_appends_with_newline(fresh_home):
    bm.set_memory("first", mutated_by="brain")
    bm.add_memory("second", mutated_by="brain")
    assert bm.get_memory() == "first\nsecond"
    assert bm.add_memory("third", mutated_by="brain")["char_count"] == len("first\nsecond\nthird")


def test_add_memory_to_empty_skips_separator(fresh_home):
    bm.add_memory("only", mutated_by="brain")
    assert bm.get_memory() == "only"


def test_add_memory_over_cap_raises(fresh_home):
    bm.set_memory("x" * 1900, mutated_by="brain")
    pre_history = _history_count()
    with pytest.raises(bm.BrainMemoryTooLargeError):
        bm.add_memory("y" * 200, mutated_by="brain")  # would total 1900+1+200 = 2101
    assert bm.get_memory() == "x" * 1900
    assert _history_count() == pre_history


def test_add_memory_empty_addition_is_noop(fresh_home):
    bm.set_memory("base", mutated_by="brain")
    pre_history = _history_count()
    out = bm.add_memory("", mutated_by="brain")
    assert out["ok"] is True
    assert out.get("noop") is True
    assert bm.get_memory() == "base"
    assert _history_count() == pre_history


def test_remove_memory_section_strips_substring(fresh_home):
    bm.set_memory("alpha-beta-gamma", mutated_by="brain")
    out = bm.remove_memory_section("beta-", mutated_by="brain")
    assert out["ok"] is True
    assert bm.get_memory() == "alpha-gamma"
    history = bm.list_history()
    assert history[0]["mutation_type"] == "remove"


def test_remove_memory_section_missing_returns_not_found(fresh_home):
    bm.set_memory("alpha", mutated_by="brain")
    pre_history = _history_count()
    out = bm.remove_memory_section("zzz", mutated_by="brain")
    assert out == {"ok": False, "reason": "not_found"}
    assert bm.get_memory() == "alpha"
    assert _history_count() == pre_history


def test_remove_memory_section_empty_needle_rejected(fresh_home):
    bm.set_memory("alpha", mutated_by="brain")
    pre_history = _history_count()
    out = bm.remove_memory_section("", mutated_by="brain")
    assert out["ok"] is False
    assert out["reason"] == "empty_needle"
    assert _history_count() == pre_history


def test_history_excerpt_truncates_at_200_chars(fresh_home):
    long_body = "a" * 500
    bm.set_memory(long_body, mutated_by="brain")
    history = bm.list_history()
    assert history[0]["after_excerpt"] == "a" * 200
    assert history[0]["before_excerpt"] == ""


def test_list_history_limit_and_ordering(fresh_home):
    for i in range(25):
        bm.set_memory(f"body-{i}", mutated_by="brain")
    history = bm.list_history(limit=20)
    assert len(history) == 20
    # Newest first — last write was body-24.
    assert history[0]["after_excerpt"] == "body-24"
    assert history[-1]["after_excerpt"] == "body-5"


def test_list_history_default_limit_is_20(fresh_home):
    for i in range(30):
        bm.set_memory(f"v{i}", mutated_by="brain")
    assert len(bm.list_history()) == 20


def test_concurrent_add_memory_serializes_safely(fresh_home):
    n_threads = 8
    payload = "abc"
    errors: list[BaseException] = []

    def worker():
        try:
            bm.add_memory(payload, mutated_by="brain")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    body = bm.get_memory()
    # First add has no leading newline; subsequent adds prepend "\n".
    expected_chars = len(payload) * n_threads + (n_threads - 1)
    assert len(body) == expected_chars
    # Each segment must be the payload, separated by newlines.
    assert body.split("\n") == [payload] * n_threads
    assert _history_count() == n_threads


def test_history_records_actor(fresh_home):
    bm.set_memory("hi", mutated_by="brain")
    bm.add_memory("there", mutated_by="operator")
    bm.remove_memory_section("there", mutated_by="auditor")
    actors = [row["mutated_by"] for row in bm.list_history(limit=10)]
    assert actors[:3] == ["auditor", "operator", "brain"]
