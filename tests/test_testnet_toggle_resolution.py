import pytest

try:
    from axiom import daemon
    from axiom.api_domains import trading as trading_domain
    from axiom.exchange import hyperliquid as hl
    _HAS_HYPERLIQUID = True
except (ImportError, ModuleNotFoundError):
    _HAS_HYPERLIQUID = False

pytestmark = pytest.mark.skipif(not _HAS_HYPERLIQUID, reason="hyperliquid package not installed")


def test_api_resolve_exchange_testnet_uses_settings_when_creds_unavailable(AXIOM_db, monkeypatch):
    monkeypatch.setattr(trading_domain, "get_execution_mode", lambda: "live")
    monkeypatch.setattr(
        trading_domain,
        "kv_get",
        lambda key, default=None: {"hyperliquid_testnet": True} if key == "axiom:settings" else (default or {}),
    )
    monkeypatch.setattr(hl, "_get_creds", lambda: (_ for _ in ()).throw(FileNotFoundError("missing creds")))

    assert trading_domain._resolve_exchange_testnet() is True


def test_api_resolve_exchange_testnet_prefers_creds_when_present(AXIOM_db, monkeypatch):
    monkeypatch.setattr(trading_domain, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(
        trading_domain,
        "kv_get",
        lambda key, default=None: {"hyperliquid_testnet": True} if key == "axiom:settings" else (default or {}),
    )
    monkeypatch.setattr(hl, "_get_creds", lambda: {"USE_TESTNET": "false"})

    assert trading_domain._resolve_exchange_testnet() is False


def test_daemon_get_testnet_uses_settings_when_creds_unavailable(monkeypatch):
    monkeypatch.setattr(
        daemon,
        "kv_get",
        lambda key, default=None: {"hyperliquid_testnet": False} if key == "axiom:settings" else (default or {}),
    )
    monkeypatch.setattr(daemon, "_get_creds", lambda: (_ for _ in ()).throw(FileNotFoundError("missing creds")))

    assert daemon._get_testnet() is False
