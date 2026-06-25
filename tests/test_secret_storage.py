from __future__ import annotations

import json

from cryptography.fernet import Fernet
import pytest

from axiom import api_core
from axiom.auth import store as auth_store


@pytest.mark.parametrize("provider", ["openai", "minimax"])
def test_auth_store_encrypts_tokens_at_rest(tmp_path, monkeypatch, provider: str):
    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    auth_file = tmp_path / "auth.json"
    monkeypatch.setattr(auth_store, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_store, "LOCK_PATH", auth_file.with_suffix(".lock"))

    auth_store.upsert_profile(
        provider,
        {
            "provider": provider,
            "type": "oauth",
            "access": "access-secret",
            "refresh": "refresh-secret",
        },
    )

    raw = json.loads(auth_file.read_text(encoding="utf-8"))
    stored_profile = raw["profiles"][f"{provider}:default"]

    assert stored_profile["access"] != "access-secret"
    assert stored_profile["refresh"] != "refresh-secret"
    assert auth_store.get_profile(provider)["access"] == "access-secret"
    assert auth_store.get_profile(provider)["refresh"] == "refresh-secret"


def test_api_key_payloads_are_encrypted_before_kv_write(monkeypatch):
    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    stored: dict[str, object] = {}

    def _fake_kv_set(key: str, value: object) -> None:
        stored[key] = value

    monkeypatch.setattr(api_core, "kv_set", _fake_kv_set)

    api_core._save_api_keys_payload(
        {
            "tiingo": {
                "value": "secret-api-key",
                "last_tested": None,
                "test_status": None,
            }
        }
    )

    raw = stored[api_core._SETTINGS_API_KEYS_STORAGE_KEY]
    assert isinstance(raw, dict)
    assert raw["tiingo"]["value"] != "secret-api-key"

    monkeypatch.setattr(api_core, "kv_get", lambda key, default=None: raw)
    loaded = api_core._load_api_keys_payload()
    assert loaded["tiingo"]["value"] == "secret-api-key"


def test_settings_secrets_round_trip_encrypted(monkeypatch):
    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    stored: dict[str, object] = {}

    def _fake_kv_set(key: str, value: object) -> None:
        stored[key] = value

    monkeypatch.setattr(api_core, "kv_set", _fake_kv_set)

    api_core._save_settings_secrets({"hyperliquid_private_key": "hl-secret"})

    raw = stored[api_core._SETTINGS_SECRET_STORAGE_KEY]
    assert isinstance(raw, dict)
    assert raw["hyperliquid_private_key"] != "hl-secret"

    monkeypatch.setattr(api_core, "kv_get", lambda key, default=None: raw)
    loaded = api_core._load_settings_secrets()
    assert loaded["hyperliquid_private_key"] == "hl-secret"


def test_load_auth_preserves_undecryptable_profiles(tmp_path, monkeypatch):
    """Undecryptable profiles must be preserved, not dropped.

    Regression for an overnight data-loss mode: when the key file briefly goes
    missing and a fresh one is generated, existing ciphertext becomes
    unreadable. Silently dropping those profiles and then round-tripping the
    store overwrites yesterday's encrypted tokens with nothing — permanently
    erasing them even if the correct key later returns.
    """
    valid_key = Fernet.generate_key().decode("utf-8")
    invalid_key = Fernet.generate_key().decode("utf-8")
    auth_file = tmp_path / "auth.json"

    monkeypatch.setattr(auth_store, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_store, "LOCK_PATH", auth_file.with_suffix(".lock"))

    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", valid_key)
    valid_access = auth_store.encrypt_secret("valid-token")

    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", invalid_key)
    invalid_access = auth_store.encrypt_secret("invalid-token")
    original_invalid_ciphertext = invalid_access  # capture for round-trip check

    auth_file.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "openai:default": {
                        "provider": "openai",
                        "access": valid_access,
                    },
                    "minimax:default": {
                        "provider": "minimax",
                        "access": invalid_access,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", valid_key)

    store = auth_store.load_auth()

    # Opaque profile is kept in the store so it can round-trip intact.
    assert "openai:default" in store["profiles"]
    assert "minimax:default" in store["profiles"]
    assert auth_store.is_profile_opaque(store["profiles"]["minimax:default"])

    # Consumers never see ciphertext as a plaintext token.
    assert auth_store.get_profile("openai")["access"] == "valid-token"
    assert auth_store.get_profile("minimax") is None

    # Re-saving (e.g. upserting openai) MUST NOT erase the minimax ciphertext.
    auth_store.upsert_profile(
        "openai", {"provider": "openai", "access": "new-openai-token"}
    )
    on_disk = json.loads(auth_file.read_text(encoding="utf-8"))
    assert on_disk["profiles"]["minimax:default"]["access"] == original_invalid_ciphertext
    # And the opaque marker is not persisted to disk.
    assert "__opaque__" not in on_disk["profiles"]["minimax:default"]


def test_get_auth_providers_marks_opaque_profiles_needs_reauth(tmp_path, monkeypatch):
    valid_key = Fernet.generate_key().decode("utf-8")
    invalid_key = Fernet.generate_key().decode("utf-8")
    auth_file = tmp_path / "auth.json"

    monkeypatch.setattr(auth_store, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_store, "LOCK_PATH", auth_file.with_suffix(".lock"))
    monkeypatch.setattr(api_core, "AUTH_FILE", auth_file)

    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", invalid_key)
    bad_access = auth_store.encrypt_secret("bad-token")
    auth_file.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "openai:default": {
                        "provider": "openai",
                        "access": bad_access,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", valid_key)

    payload = api_core.get_auth_providers()

    provider_map = {item["provider"]: item for item in payload["providers"]}
    assert provider_map["openai"]["configured"] is False
    assert provider_map["openai"]["status"] == "needs_reauth"
    # Providers with no stored profile at all still report not_configured.
    assert provider_map["minimax"]["status"] == "not_configured"


def test_save_auth_rotates_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    auth_file = tmp_path / "auth.json"
    monkeypatch.setattr(auth_store, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_store, "LOCK_PATH", auth_file.with_suffix(".lock"))

    auth_store.upsert_profile("openai", {"provider": "openai", "access": "one"})
    # First save creates the file; no backup yet.
    assert auth_file.exists()
    assert not auth_file.with_name(auth_file.name + ".bak.1").exists()

    auth_store.upsert_profile("openai", {"provider": "openai", "access": "two"})
    assert auth_file.with_name(auth_file.name + ".bak.1").exists()

    auth_store.upsert_profile("openai", {"provider": "openai", "access": "three"})
    assert auth_file.with_name(auth_file.name + ".bak.1").exists()
    assert auth_file.with_name(auth_file.name + ".bak.2").exists()

    auth_store.upsert_profile("openai", {"provider": "openai", "access": "four"})
    # Latest backup should reflect the previous save, not the current one.
    latest_bak = json.loads(
        auth_file.with_name(auth_file.name + ".bak.1").read_text(encoding="utf-8")
    )
    current = json.loads(auth_file.read_text(encoding="utf-8"))
    assert latest_bak != current

    # Rotation is bounded — .bak.4 is never created (count is 3).
    auth_store.upsert_profile("openai", {"provider": "openai", "access": "five"})
    assert not auth_file.with_name(auth_file.name + ".bak.4").exists()


def test_fernet_cache_prevents_regen_when_key_file_disappears(tmp_path, monkeypatch):
    """If the key file transiently disappears, we must NOT generate a fresh
    key that would orphan previously-encrypted data."""
    from axiom import config as cfg
    from axiom import secret_storage

    monkeypatch.delenv("AXIOM_ENCRYPTION_KEY", raising=False)
    key_dir = tmp_path / "appdata"
    key_dir.mkdir()
    key_file = key_dir / ".axiom_key"

    # Isolate cfg.AUTH_FILE so the existing-ciphertext guard in
    # _load_fernet_key looks at an empty tmp path, not the real auth store.
    monkeypatch.setattr(cfg, "AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(secret_storage, "_preferred_key_path", lambda: key_file)
    monkeypatch.setattr(secret_storage, "_legacy_key_path", lambda: key_file)
    secret_storage._reset_cache_for_tests()

    # First use generates and caches the key.
    ciphertext = secret_storage.encrypt_secret("payload")
    original_key_bytes = key_file.read_bytes()

    # Simulate the key file vanishing (Drive sync race, tmp cleanup, etc.).
    key_file.unlink()

    # Decrypting must still succeed — cache preserves the key.
    assert secret_storage.decrypt_secret(ciphertext) == "payload"

    # And the file is restored to the same key, not a new one.
    assert key_file.exists()
    assert key_file.read_bytes().strip() == original_key_bytes.strip()


def test_key_migration_copies_legacy_to_preferred(tmp_path, monkeypatch):
    from axiom import config as cfg
    from axiom import secret_storage

    monkeypatch.delenv("AXIOM_ENCRYPTION_KEY", raising=False)
    legacy_file = tmp_path / "legacy" / ".axiom_key"
    preferred_file = tmp_path / "preferred" / ".axiom_key"
    legacy_file.parent.mkdir()
    legacy_file.write_text(Fernet.generate_key().decode("utf-8") + "\n", encoding="utf-8")

    monkeypatch.setattr(cfg, "AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(secret_storage, "_preferred_key_path", lambda: preferred_file)
    monkeypatch.setattr(secret_storage, "_legacy_key_path", lambda: legacy_file)
    secret_storage._reset_cache_for_tests()

    # First access triggers migration into the preferred (non-synced) path.
    secret_storage.encrypt_secret("anything")

    assert preferred_file.exists()
    assert preferred_file.read_text(encoding="utf-8") == legacy_file.read_text(encoding="utf-8")
    # Legacy file is left in place for backward compatibility.
    assert legacy_file.exists()


def test_key_generation_refused_when_ciphertext_exists(tmp_path, monkeypatch):
    """Regression: on Apr 17 a fresh key was generated at %LOCALAPPDATA% while
    an auth.json full of legacy-encrypted profiles still lived on disk. That
    orphaned every provider. Refuse to generate in that state."""
    from axiom import config as cfg
    from axiom import secret_storage

    monkeypatch.delenv("AXIOM_ENCRYPTION_KEY", raising=False)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {"version": 1, "profiles": {"openai:default": {"access": "fernet:abc123"}}}
        ),
        encoding="utf-8",
    )
    key_file = tmp_path / ".axiom_key"

    monkeypatch.setattr(cfg, "AUTH_FILE", auth_file)
    monkeypatch.setattr(secret_storage, "_preferred_key_path", lambda: key_file)
    monkeypatch.setattr(secret_storage, "_legacy_key_path", lambda: key_file)
    secret_storage._reset_cache_for_tests()

    with pytest.raises(RuntimeError, match="Refusing to generate"):
        secret_storage.encrypt_secret("anything")

    # Guard didn't write a new key file.
    assert not key_file.exists()
