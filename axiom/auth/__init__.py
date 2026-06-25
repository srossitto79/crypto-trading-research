"""Auth module — OAuth flows and token management."""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("axiom.auth")


# H-S5: trusted issuers for OAuth access tokens we parse for metadata.
# A token whose `iss` claim doesn't match one of these is rejected even
# though we can't verify the signature without JWKS keys.
_TRUSTED_ISSUERS = (
    "https://auth.openai.com",
    "https://auth0.openai.com",
    "https://accounts.openai.com",
)


def safe_extract_chatgpt_account_id(access_token: str) -> Optional[str]:
    """Extract chatgpt_account_id from an OpenAI OAuth access token.

    H-S5: signature verification is intentionally disabled — we don't have
    the OpenAI JWKS keys configured, and the token came from a TLS-verified
    OAuth code-exchange call so the transport is the trust anchor. To prevent
    a malicious response from injecting a bogus account_id we still:
      1. Validate the token's `iss` claim is an OpenAI issuer (when present)
      2. Validate the extracted value is a string of reasonable shape
      3. Return None on any anomaly rather than raising
    """
    if not access_token or not isinstance(access_token, str):
        return None
    try:
        import jwt
    except Exception:
        log.warning("PyJWT not available; cannot extract account_id from access_token")
        return None
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
    except Exception as exc:
        log.debug("Failed to decode OAuth access_token: %s", exc)
        return None
    if not isinstance(claims, dict):
        return None

    issuer = str(claims.get("iss", "") or "").strip()
    if issuer and not any(issuer.startswith(prefix) for prefix in _TRUSTED_ISSUERS):
        log.warning("Rejecting access_token with untrusted iss=%r", issuer)
        return None

    auth_claim = claims.get("https://api.openai.com/auth")
    if not isinstance(auth_claim, dict):
        return None

    account_id = auth_claim.get("chatgpt_account_id")
    if not isinstance(account_id, str):
        return None
    account_id = account_id.strip()
    if not account_id or len(account_id) > 128:
        return None
    if not all(c.isalnum() or c in "-_" for c in account_id):
        return None
    return account_id
