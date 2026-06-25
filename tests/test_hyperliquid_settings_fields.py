"""HyperLiquid settings aliases for wallet/api-address/api-secret fields."""

from __future__ import annotations


def test_hyperliquid_settings_accept_actual_wallet_api_fields(AXIOM_db):
    import axiom.api_core as core

    payload = core._apply_settings_section(
        "hyperliquid",
        {
            "actual_wallet_address": "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
            "api_address": "0xFaceFeedFaceFeedFaceFeedFaceFeedFaceFeed",
            "api_secret_key": "0x" + ("1" * 64),
            "use_testnet": True,
        },
    )

    assert payload["hyperliquid_wallet"] == "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF"
    assert payload["hyperliquid_api_address"] == "0xFaceFeedFaceFeedFaceFeedFaceFeedFaceFeed"
    assert payload["hyperliquid_has_key"] is True
    assert payload["hyperliquid_testnet"] is True

    secrets = core.kv_get("axiom:settings:secrets", {}) or {}
    assert secrets.get("hyperliquid_private_key") == "0x" + ("1" * 64)


def test_hyperliquid_settings_accept_canonical_field_names(AXIOM_db):
    import axiom.api_core as core

    payload = core._apply_settings_section(
        "hyperliquid",
        {
            "hyperliquid_wallet": "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
            "hyperliquid_api_address": "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe",
            "hyperliquid_private_key": "0x" + ("2" * 64),
            "hyperliquid_testnet": False,
        },
    )

    assert payload["hyperliquid_wallet"] == "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF"
    assert payload["hyperliquid_api_address"] == "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe"
    assert payload["hyperliquid_has_key"] is True
    assert payload["hyperliquid_testnet"] is False

    secrets = core.kv_get("axiom:settings:secrets", {}) or {}
    assert secrets.get("hyperliquid_private_key") == "0x" + ("2" * 64)


def test_hyperliquid_private_key_update_preserves_existing_api_address(AXIOM_db):
    import axiom.api_core as core

    core._apply_settings_section(
        "hyperliquid",
        {
            "actual_wallet_address": "0xDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeFDeaDbEeF",
            "api_address": "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe",
            "api_secret_key": "0x" + ("1" * 64),
            "use_testnet": True,
        },
    )
    payload = core._apply_settings_section(
        "hyperliquid",
        {
            "api_secret_key": "0x" + ("3" * 64),
        },
    )

    assert payload["hyperliquid_api_address"] == "0xCafeBabeCafeBabeCafeBabeCafeBabeCafeBabe"
