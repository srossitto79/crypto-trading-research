from pathlib import Path

from axiom.migration.juddex_to_axiom import migrate_home_directory


def _fake_home(tmp_path, monkeypatch) -> Path:
    """Create an isolated fake home dir and redirect Path.home() to it.

    Using a tmp_path subdir avoids collision with conftest's autouse
    `_isolate_AXIOM_home` fixture, which creates `tmp_path/.Axiom`.
    """
    fake = tmp_path / "fake_home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setattr(Path, "home", lambda: fake)
    return fake


def test_migration_moves_legacy_home(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    legacy = home / ".juddex"
    legacy.mkdir()
    (legacy / "juddex.duckdb").write_bytes(b"dbdata")
    (legacy / ".juddex_key").write_text("key")

    moved = migrate_home_directory()

    assert moved is True
    current = home / ".Axiom"
    assert (current / "axiom.duckdb").read_bytes() == b"dbdata"
    assert (current / ".axiom_key").read_text() == "key"
    assert (home / ".juddex" / "LEGACY_JUddEX_MOVED_TO_AXIOM").exists()


def test_migration_skips_when_AXIOM_home_exists(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    (home / ".juddex").mkdir()
    current = home / ".Axiom"
    current.mkdir()
    (current / "sentinel").write_text("already here")

    moved = migrate_home_directory()

    assert moved is False
    assert (home / ".juddex").exists()


def test_migration_skips_when_legacy_absent(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)

    moved = migrate_home_directory()

    assert moved is False
