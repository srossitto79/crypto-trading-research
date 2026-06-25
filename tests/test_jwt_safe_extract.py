"""Regression tests for H-S5 (validated JWT extraction)."""

from __future__ import annotations

import jwt

from axiom.auth import safe_extract_chatgpt_account_id


def _mint(payload: dict) -> str:
    """Mint an unsigned JWT for testing — we don't verify signatures here,
    only the structural validation logic."""
    return jwt.encode(payload, key="dummy", algorithm="HS256")


def test_extract_returns_account_id_for_trusted_issuer():
    token = _mint({
        "iss": "https://auth.openai.com/",
        "https://api.openai.com/auth": {"chatgpt_account_id": "acc-12345_abc"},
    })
    assert safe_extract_chatgpt_account_id(token) == "acc-12345_abc"


def test_extract_rejects_untrusted_issuer():
    token = _mint({
        "iss": "https://evil.example.com/",
        "https://api.openai.com/auth": {"chatgpt_account_id": "acc-evil"},
    })
    assert safe_extract_chatgpt_account_id(token) is None


def test_extract_accepts_token_without_iss_claim():
    """Some legacy tokens don't include iss; we accept them but still validate value."""
    token = _mint({
        "https://api.openai.com/auth": {"chatgpt_account_id": "acc-noiss"},
    })
    assert safe_extract_chatgpt_account_id(token) == "acc-noiss"


def test_extract_rejects_non_string_account_id():
    token = _mint({
        "iss": "https://auth.openai.com/",
        "https://api.openai.com/auth": {"chatgpt_account_id": 12345},
    })
    assert safe_extract_chatgpt_account_id(token) is None


def test_extract_rejects_account_id_with_path_traversal():
    token = _mint({
        "iss": "https://auth.openai.com/",
        "https://api.openai.com/auth": {"chatgpt_account_id": "../../etc/passwd"},
    })
    assert safe_extract_chatgpt_account_id(token) is None


def test_extract_rejects_account_id_too_long():
    token = _mint({
        "iss": "https://auth.openai.com/",
        "https://api.openai.com/auth": {"chatgpt_account_id": "a" * 200},
    })
    assert safe_extract_chatgpt_account_id(token) is None


def test_extract_rejects_malformed_token():
    assert safe_extract_chatgpt_account_id("not.a.jwt") is None
    assert safe_extract_chatgpt_account_id("") is None
    assert safe_extract_chatgpt_account_id(None) is None  # type: ignore[arg-type]


def test_extract_rejects_missing_auth_claim():
    token = _mint({"iss": "https://auth.openai.com/", "sub": "user-1"})
    assert safe_extract_chatgpt_account_id(token) is None
