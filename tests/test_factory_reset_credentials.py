from __future__ import annotations


def _setup_isolated_auth(tmp_path, monkeypatch):
    """Isolate auth.json + key file into tmp_path and seed two credential
    profiles, so a factory_reset test can't touch the live user store.

    Returns the auth_store module with two profiles ('openai', 'minimax').
    """
    from cryptography.fernet import Fernet

    from axiom import config as cfg
    from axiom import db as AXIOM_db_mod
    from axiom import secret_storage
    from axiom.auth import store as auth_store

    auth_file = tmp_path / "auth.json"
    key_file = tmp_path / ".axiom_key"
    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(cfg, "AUTH_FILE", auth_file)
    monkeypatch.setattr(cfg, "AXIOM_HOME", tmp_path)
    monkeypatch.setattr(auth_store, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_store, "LOCK_PATH", auth_file.with_suffix(".lock"))
    monkeypatch.setattr(AXIOM_db_mod, "AUTH_FILE", auth_file)
    monkeypatch.setattr(AXIOM_db_mod, "AXIOM_HOME", tmp_path)
    monkeypatch.setattr(secret_storage, "_preferred_key_path", lambda: key_file)
    monkeypatch.setattr(secret_storage, "_legacy_key_path", lambda: key_file)
    secret_storage._reset_cache_for_tests()

    auth_store.upsert_profile("openai", {"provider": "openai", "access": "test-openai-token"})
    auth_store.upsert_profile("minimax", {"provider": "minimax", "access": "test-minimax-token"})
    assert auth_store.get_profile("openai") is not None
    assert auth_store.get_profile("minimax") is not None
    return auth_store


def test_factory_reset_defaults_preserve_credentials(AXIOM_db, tmp_path, monkeypatch):
    """Unspecified keep (None) falls back to the default_keep set — credentials stay."""
    from axiom import db as AXIOM_db_mod

    auth_store = _setup_isolated_auth(tmp_path, monkeypatch)

    result = AXIOM_db_mod.factory_reset()  # None -> use default_keep

    assert result["status"] == "ok"
    assert "credentials" in result["kept"]
    assert auth_store.get_profile("openai") is not None
    assert auth_store.get_profile("minimax") is not None


def test_factory_reset_explicit_empty_protects_credentials_by_default(AXIOM_db, tmp_path, monkeypatch):
    """Even an explicit empty keep list ('wipe everything') must NOT drop credentials
    unless the caller explicitly opts in — the guard against the recurring
    'credentials dropped' incident (agent tools / buggy callers omitting credentials)."""
    from axiom import db as AXIOM_db_mod

    auth_store = _setup_isolated_auth(tmp_path, monkeypatch)

    result = AXIOM_db_mod.factory_reset([])  # explicit wipe-all, but NO opt-in

    assert result["status"] == "ok"
    assert result["credentials_protected"] is True
    assert "credentials" in result["kept"]
    assert "credentials" not in result["wiped"]
    assert auth_store.get_profile("openai") is not None
    assert auth_store.get_profile("minimax") is not None


def test_factory_reset_wipes_credentials_only_with_explicit_optin(AXIOM_db, tmp_path, monkeypatch):
    """allow_credentials_wipe=True is the only way to actually drop credentials —
    and it also clears the backups so the auth self-heal can't restore them."""
    from pathlib import Path

    from axiom import config as cfg
    from axiom import db as AXIOM_db_mod

    auth_store = _setup_isolated_auth(tmp_path, monkeypatch)

    result = AXIOM_db_mod.factory_reset([], allow_credentials_wipe=True)

    assert result["status"] == "ok"
    assert result["credentials_protected"] is False
    assert "credentials" in result["wiped"]
    assert auth_store.get_profile("openai") is None
    assert auth_store.get_profile("minimax") is None
    assert not Path(cfg.AUTH_FILE).exists()
    for n in range(1, 4):
        assert not (tmp_path / f"auth.json.bak.{n}").exists()
