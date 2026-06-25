import pytest


@pytest.fixture
def tmp_strategy(AXIOM_db, tmp_path, monkeypatch):
    custom_dir = tmp_path / "Axiom" / "strategies" / "custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "S99001.py").write_text("# my strategy\nclass Foo: pass\n")
    monkeypatch.setenv("AXIOM_STRATEGIES_CUSTOM_DIR", str(custom_dir))
    return "S99001"


def test_read_strategy_code_returns_source(tmp_strategy):
    from axiom.agents.tools_deepdive import _read_strategy_code, set_deepdive_strategy
    set_deepdive_strategy(tmp_strategy)
    src = _read_strategy_code()
    assert "class Foo" in src


def test_read_strategy_code_without_session_raises():
    from axiom.agents.tools_deepdive import _read_strategy_code, clear_deepdive_strategy
    clear_deepdive_strategy()
    with pytest.raises(RuntimeError, match="no Deepdive strategy"):
        _read_strategy_code()


def test_read_strategy_code_missing_file_raises(AXIOM_db, tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_STRATEGIES_CUSTOM_DIR", str(tmp_path))
    from axiom.agents.tools_deepdive import _read_strategy_code, set_deepdive_strategy
    set_deepdive_strategy("S99099")
    with pytest.raises(FileNotFoundError):
        _read_strategy_code()
