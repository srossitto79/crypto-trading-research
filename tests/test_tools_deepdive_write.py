import pytest
from axiom.db import get_db


@pytest.fixture
def tmp_strategy(AXIOM_db, tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    (custom_dir / "S88001.py").write_text("class Foo: pass\n")
    monkeypatch.setenv("AXIOM_STRATEGIES_CUSTOM_DIR", str(custom_dir))
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO strategies (id, name) VALUES (?, ?)",
            ("S88001", "test"),
        )
        conn.commit()
    return "S88001", custom_dir


def test_write_valid_python_persists(tmp_strategy):
    sid, d = tmp_strategy
    from axiom.agents.tools_deepdive import _write_strategy_code, set_deepdive_strategy
    set_deepdive_strategy(sid)
    new_src = "class Bar:\n    pass\n"
    result = _write_strategy_code(new_source=new_src, rationale="rename Foo->Bar", thread_id="dd_test")
    assert "wrote" in result.lower()
    assert (d / "S88001.py").read_text() == new_src


def test_write_invalid_syntax_rejects(tmp_strategy):
    sid, d = tmp_strategy
    from axiom.agents.tools_deepdive import _write_strategy_code, set_deepdive_strategy
    set_deepdive_strategy(sid)
    with pytest.raises(SyntaxError):
        _write_strategy_code(new_source="def broken(:\n", rationale="oops", thread_id="dd_test")
    # Original file unchanged
    assert "class Foo" in (d / "S88001.py").read_text()


def test_write_logs_to_activity(tmp_strategy):
    sid, _d = tmp_strategy
    from axiom.agents.tools_deepdive import _write_strategy_code, set_deepdive_strategy
    set_deepdive_strategy(sid)
    _write_strategy_code(new_source="class Baz: pass\n", rationale="r", thread_id="dd_test")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT source, message, data FROM activity_log "
            "WHERE source LIKE 'deepdive_agent:%' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert rows, "expected an activity_log row from deepdive write"
    source, message, _data = rows[0]
    assert source == "deepdive_agent:dd_test"
    assert "S88001" in message or "wrote" in message.lower()
