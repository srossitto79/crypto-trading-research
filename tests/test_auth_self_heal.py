"""auth.json self-heals from a backup when it goes missing/corrupt, but an
intentional logout (a valid, smaller store) is honoured — not resurrected.

Regression for the recurring "it dropped our auth" failure: a lost/corrupt
auth.json used to leave the app running with NO credentials until an operator
re-logged-in, even though rotating backups were sitting right next to it.
"""
from __future__ import annotations


def _isolate_auth(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from axiom import config as cfg
    from axiom import secret_storage
    from axiom.auth import store as auth_store

    auth_file = tmp_path / "auth.json"
    key_file = tmp_path / ".axiom_key"
    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(cfg, "AUTH_FILE", auth_file)
    monkeypatch.setattr(cfg, "AXIOM_HOME", tmp_path)
    monkeypatch.setattr(auth_store, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_store, "LOCK_PATH", auth_file.with_suffix(".lock"))
    monkeypatch.setattr(secret_storage, "_preferred_key_path", lambda: key_file)
    monkeypatch.setattr(secret_storage, "_legacy_key_path", lambda: key_file)
    secret_storage._reset_cache_for_tests()
    return auth_store


def test_missing_auth_self_heals_from_backup(tmp_path, monkeypatch):
    store = _isolate_auth(tmp_path, monkeypatch)
    # Two saves so a backup (.bak.1) is rotated into existence.
    store.upsert_profile("openai", {"provider": "openai", "access": "tok-openai"})
    store.upsert_profile("minimax", {"provider": "minimax", "access": "tok-minimax"})
    assert store._bak_path(1).exists()

    # Simulate the drop: auth.json vanishes.
    store.AUTH_FILE.unlink()
    assert not store.AUTH_FILE.exists()

    # load_auth should recover from the backup and rewrite auth.json.
    recovered = store.load_auth()
    assert "openai:default" in recovered["profiles"]
    assert store.AUTH_FILE.exists()
    assert store.get_profile("openai") is not None


def test_corrupt_auth_self_heals_from_backup(tmp_path, monkeypatch):
    store = _isolate_auth(tmp_path, monkeypatch)
    store.upsert_profile("openai", {"provider": "openai", "access": "tok-openai"})
    store.upsert_profile("minimax", {"provider": "minimax", "access": "tok-minimax"})
    assert store._bak_path(1).exists()

    # Corrupt the main file (undecryptable garbage).
    store.AUTH_FILE.write_text("}{ not json at all")

    recovered = store.load_auth()
    assert recovered["profiles"]  # non-empty — recovered, not blanked
    assert store.get_profile("openai") is not None


def test_intentional_logout_is_not_resurrected(tmp_path, monkeypatch):
    store = _isolate_auth(tmp_path, monkeypatch)
    store.upsert_profile("openai", {"provider": "openai", "access": "tok-openai"})
    store.upsert_profile("minimax", {"provider": "minimax", "access": "tok-minimax"})

    # Log out everything via a normal write (the real logout path): valid empty store.
    store.save_auth({"version": 1, "profiles": {}})

    loaded = store.load_auth()
    assert loaded["profiles"] == {}  # honoured — NOT recovered from the backup
    assert store.get_profile("openai") is None
