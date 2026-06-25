"""Tests for HyperLiquid price payload parsing and public mids fallback."""

from __future__ import annotations

import pytest

try:
    import hyperliquid  # noqa: F401
    _HAS_HYPERLIQUID = True
except ImportError:
    _HAS_HYPERLIQUID = False

pytestmark = pytest.mark.skipif(not _HAS_HYPERLIQUID, reason="hyperliquid package not installed")


def test_resolve_price_payload_handles_ws_data_dict():
    from axiom.exchange.hyperliquid import _resolve_price_payload

    payload = {
        "channel": "allMids",
        "data": {
            "BTC": "66935.5",
            "ETH": "2008.35",
            "SOL": "85.8075",
        },
    }
    prices = _resolve_price_payload(payload)
    assert prices["BTC"] == 66935.5
    assert prices["ETH"] == 2008.35
    assert prices["SOL"] == 85.8075


def test_resolve_price_payload_handles_nested_mids():
    from axiom.exchange.hyperliquid import _resolve_price_payload

    payload = {
        "channel": "allMids",
        "data": {
            "mids": {
                "BTC": "67000",
                "ETH": "2000",
            },
            "ts": 1772375000,
        },
    }
    prices = _resolve_price_payload(payload)
    assert prices["BTC"] == 67000.0
    assert prices["ETH"] == 2000.0


def test_get_all_mids_uses_public_client_when_creds_missing(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    class _DummyInfo:
        def all_mids(self):
            return {"BTC": "1.5", "ETH": "2.5"}

    def _raise_missing(*_args, **_kwargs):
        raise FileNotFoundError("missing creds")

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(hl.hl_price_breaker, "can_execute", lambda: True)
    monkeypatch.setattr(hl, "get_exchange", _raise_missing)
    monkeypatch.setattr(hl, "_get_public_info_client", lambda testnet: _DummyInfo())
    monkeypatch.setattr(hl, "_with_breaker", lambda _name, _breaker, fn, *a, **k: fn(*a, **k))

    prices = hl.get_all_mids(testnet=True)
    assert prices == {"BTC": 1.5, "ETH": 2.5}


def test_get_all_mids_uses_cached_prices_when_breaker_open(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(hl.hl_price_breaker, "can_execute", lambda: False)
    monkeypatch.setattr(hl, "kv_get", lambda key, default=None: {"last_prices": {"BTC": "100.0", "ETH": 50}} if key == "daemon_state" else default)

    prices = hl.get_all_mids(testnet=True)
    assert prices == {"BTC": 100.0, "ETH": 50.0}


def test_get_creds_plaintext_when_encryption_disabled(monkeypatch, tmp_path):
    import json
    import axiom.exchange.hyperliquid as hl

    settings = {
        "HL_API_SECRET": "plain-secret",
        "HL_API_KEY": "plain-key",
        "HL_WALLET_ADDRESS": "0xabc",
        "USE_TESTNET": "true",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    monkeypatch.setenv("AXIOM_HL_CREDS_PATH", str(tmp_path))
    monkeypatch.setenv("AXIOM_HL_DISABLE_ENCRYPTION", "1")

    creds = hl._get_creds()
    assert creds["HL_API_SECRET"] == "plain-secret"
    assert creds["HL_API_KEY"] == "plain-key"
    assert creds["HL_WALLET_ADDRESS"] == "0xabc"
    assert str(creds["USE_TESTNET"]).lower() == "true"


def test_get_account_value_falls_back_in_paper_mode_when_creds_missing(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(
        hl,
        "_get_account_info_client",
        lambda testnet=True: (_ for _ in ()).throw(FileNotFoundError("missing creds")),
    )
    monkeypatch.setattr(hl, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(
        hl,
        "kv_get",
        lambda key, default=None: {"current_equity": 12345.0} if key == "daily_risk" else default,
    )

    account = hl.get_account_value(testnet=True)
    assert account["accountValue"] == 12345.0
    assert account["totalMarginUsed"] == 0.0


def test_get_account_value_falls_back_in_paper_mode_when_exchange_init_fails(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(
        hl,
        "_get_account_info_client",
        lambda testnet=True: (_ for _ in ()).throw(RuntimeError("bad key")),
    )
    monkeypatch.setattr(hl, "get_execution_mode", lambda: "paper")
    monkeypatch.setattr(
        hl,
        "kv_get",
        lambda key, default=None: {"start_equity": 10000.0} if key == "daily_risk" else default,
    )

    account = hl.get_account_value(testnet=True)
    assert account["accountValue"] == 10000.0
    assert account["totalMarginUsed"] == 0.0


def test_get_account_value_raises_when_connection_required(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(
        hl,
        "_get_account_info_client",
        lambda testnet=True: (_ for _ in ()).throw(RuntimeError("bad key")),
    )
    monkeypatch.setattr(hl, "get_execution_mode", lambda: "paper")

    with pytest.raises(RuntimeError):
        hl.get_account_value(testnet=True, require_connection=True)


def test_get_account_value_uses_wallet_only_settings(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    class _DummyInfo:
        def user_state(self, wallet):
            assert wallet == "0xabc123"
            return {
                "marginSummary": {
                    "accountValue": "1002.68",
                    "totalMarginUsed": "0",
                    "totalNtlPos": "0",
                    "totalRawUsd": "1002.68",
                }
            }

    def _kv_get(key, default=None):
        if key == "axiom:settings":
            return {
                "hyperliquid_wallet": "0xabc123",
                "hyperliquid_testnet": True,
            }
        if key == "axiom:settings:secrets":
            return {}
        return default

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(hl, "kv_get", _kv_get)
    monkeypatch.setattr(hl, "_get_public_info_client", lambda testnet=True: _DummyInfo())
    monkeypatch.setattr(hl, "_with_breaker", lambda _name, _breaker, fn, *a, **k: fn(*a, **k))

    account = hl.get_account_value(testnet=True, require_connection=True)
    assert account["accountValue"] == 1002.68
    assert account["totalRawUsd"] == 1002.68


def test_load_creds_from_settings_includes_api_address(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    def _kv_get(key, default=None):
        if key == "axiom:settings":
            return {
                "hyperliquid_wallet": "0xactual",
                "hyperliquid_api_address": "0xapi",
                "hyperliquid_testnet": True,
            }
        if key == "axiom:settings:secrets":
            return {"hyperliquid_private_key": "0xsecret"}
        return default

    monkeypatch.setattr(hl, "kv_get", _kv_get)
    creds = hl._load_creds_from_AXIOM_settings()
    assert creds["HL_WALLET_ADDRESS"] == "0xactual"
    assert creds["HL_API_KEY"] == "0xapi"
    assert creds["HL_API_SECRET"] == "0xsecret"


def test_get_account_value_uses_spot_usdc_when_margin_zero(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    class _DummyInfo:
        def user_state(self, wallet):
            assert wallet == "0xabc123"
            return {
                "marginSummary": {
                    "accountValue": "0",
                    "totalMarginUsed": "0",
                    "totalNtlPos": "0",
                    "totalRawUsd": "0",
                }
            }

        def spot_user_state(self, wallet):
            assert wallet == "0xabc123"
            return {
                "balances": [
                    {"coin": "USDC", "total": "1002.68", "hold": "0"},
                ]
            }

    def _kv_get(key, default=None):
        if key == "axiom:settings":
            return {
                "hyperliquid_wallet": "0xabc123",
                "hyperliquid_testnet": True,
            }
        if key == "axiom:settings:secrets":
            return {}
        return default

    monkeypatch.setattr("axiom.sim.clock.is_sim_active", lambda: False)
    monkeypatch.setattr(hl, "kv_get", _kv_get)
    monkeypatch.setattr(hl, "_get_public_info_client", lambda testnet=True: _DummyInfo())
    monkeypatch.setattr(hl, "_with_breaker", lambda _name, _breaker, fn, *a, **k: fn(*a, **k))

    account = hl.get_account_value(testnet=True, require_connection=True)
    assert account["accountValue"] == 1002.68
    assert account["totalRawUsd"] == 1002.68
    assert account["withdrawable"] == 1002.68


def test_get_exchange_sets_account_address_for_delegated_agent(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    captured = {}

    class _DummyExchange:
        def __init__(self, account, url, **kwargs):
            self.wallet = account
            self.base_url = url
            self.kwargs = kwargs
            captured["kwargs"] = kwargs

    class _DummyInfo:
        def __init__(self, _url, skip_ws=True, **_kwargs):
            self.skip_ws = skip_ws

    monkeypatch.setattr(
        hl,
        "_get_creds",
        lambda: {
            "HL_API_SECRET": "0x" + ("1" * 64),
            "HL_WALLET_ADDRESS": "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
            "USE_TESTNET": "true",
        },
    )
    monkeypatch.setattr(hl, "Exchange", _DummyExchange)
    monkeypatch.setattr(hl, "Info", _DummyInfo)

    exchange, _info, info_address = hl.get_exchange(testnet=True)
    assert info_address == "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF"
    assert captured["kwargs"]["account_address"] == "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF"
    assert "vault_address" not in captured["kwargs"]
    assert "vault_address" not in exchange.kwargs


def test_get_exchange_rejects_mismatched_api_address_and_private_key(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    monkeypatch.setattr(
        hl,
        "_get_creds",
        lambda: {
            "HL_API_SECRET": "0x" + ("1" * 64),
            "HL_API_KEY": "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe",
            "HL_WALLET_ADDRESS": "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
            "USE_TESTNET": "true",
        },
    )

    with pytest.raises(RuntimeError, match="does not match the configured private key"):
        hl.get_exchange(testnet=True)


def test_agent_authorization_check_blocks_unapproved_agent():
    import axiom.exchange.hyperliquid as hl

    class _DummyWallet:
        address = "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe"

    class _DummyExchange:
        wallet = _DummyWallet()

    class _DummyInfo:
        def extra_agents(self, _wallet):
            return [{"address": "0x1111111111111111111111111111111111111111"}]

    with pytest.raises(RuntimeError, match="not approved"):
        hl._ensure_agent_authorized_for_trading(
            _DummyExchange(),
            _DummyInfo(),
            "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
            "https://api.hyperliquid-testnet.xyz",
        )


def test_agent_authorization_lookup_warning_fails_open(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    warnings: list[str] = []

    def _fake_warning(message, *args):
        warnings.append(message % args if args else str(message))

    class _DummyInfo:
        def extra_agents(self, _wallet):
            raise OSError("agent lookup unavailable")

    hl._AGENT_AUTH_CACHE.clear()
    monkeypatch.setattr(hl.log, "warning", _fake_warning)

    assert hl._is_agent_authorized(
        _DummyInfo(),
        "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
        "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe",
        "https://api.hyperliquid-testnet.xyz",
    ) is True
    assert any("Could not verify HyperLiquid agent authorization" in warning for warning in warnings)


def test_sanitize_spot_meta_logs_invalid_token_indexes(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    warnings: list[str] = []

    def _fake_warning(message, *args):
        warnings.append(message % args if args else str(message))

    monkeypatch.setattr(hl.log, "warning", _fake_warning)

    sanitized = hl._sanitize_spot_meta(
        {
            "tokens": [
                {"name": "USDC", "szDecimals": 6},
                {"name": "PURR", "szDecimals": 2},
            ],
            "universe": [
                {"name": "bad", "tokens": ["oops", 0]},
                {"name": "PURR/USDC", "tokens": [1, 0]},
            ],
        }
    )

    assert len(sanitized["universe"]) == 1
    assert sanitized["universe"][0]["name"] == "PURR/USDC"
    assert any("Dropping malformed HyperLiquid spot pair token indexes" in warning for warning in warnings)


def test_with_breaker_logs_warning_and_reraises(monkeypatch):
    import axiom.exchange.hyperliquid as hl

    warnings: list[str] = []

    def _fake_warning(message, *args):
        warnings.append(message % args if args else str(message))

    class _Breaker:
        def __init__(self):
            self.failures = 0
            self.successes = 0

        def can_execute(self):
            return True

        def record_failure(self):
            self.failures += 1

        def record_success(self):
            self.successes += 1

    monkeypatch.setattr(hl.log, "warning", _fake_warning)
    breaker = _Breaker()

    with pytest.raises(RuntimeError, match="boom"):
        hl._with_breaker("account", breaker, lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert breaker.failures == 1
    assert breaker.successes == 0
    assert any("HyperLiquid account call failed: boom" in warning for warning in warnings)
