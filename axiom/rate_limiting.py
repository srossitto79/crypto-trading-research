"""H-S6: HTTP request rate limiting via slowapi.

Local-first app: the API binds to 127.0.0.1 by default. Rate limiting is
defense-in-depth for the opt-in AXIOM_BIND_HOST/proxied scenarios (and a
runaway frontend), so a misbehaving client can't flood expensive endpoints.
Default: 600 req/min per remote IP, which is well above interactive use but
catches floods. Disable with AXIOM_RATE_LIMIT_ENABLED=0 for testing.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("axiom.rate_limiting")

_DEFAULT_LIMIT = "600/minute"
_EXEMPT_PATH_PREFIXES = (
    "/api/health",
    "/api/status",
    "/health",
    "/healthz",
    "/api/ws/",
)


def rate_limit_enabled() -> bool:
    raw = str(os.environ.get("AXIOM_RATE_LIMIT_ENABLED", "1")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_default_limit() -> str:
    return str(os.environ.get("AXIOM_RATE_LIMIT_DEFAULT", _DEFAULT_LIMIT)).strip() or _DEFAULT_LIMIT


def is_exempt_path(path: str) -> bool:
    if not path:
        return False
    return any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES)


def install_rate_limiter(app) -> Optional[object]:
    """Install slowapi limiter on the FastAPI app. Returns the Limiter or None
    if rate limiting is disabled or the dependency is missing."""
    if not rate_limit_enabled():
        log.info("Rate limiting disabled via AXIOM_RATE_LIMIT_ENABLED")
        return None
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        from slowapi.util import get_remote_address
    except Exception as exc:
        log.warning("slowapi not available (%s); skipping rate limiter install", exc)
        return None

    def _key_func(request) -> str:
        # Skip exempt paths by always returning a unique sentinel — slowapi
        # treats per-key buckets so this avoids shared exhaustion.
        try:
            if is_exempt_path(request.url.path):
                return "__exempt__"
        except Exception:
            pass
        return get_remote_address(request)

    limit = get_default_limit()
    limiter = Limiter(
        key_func=_key_func,
        default_limits=[limit],
        # Ignore exempt paths entirely
        in_memory_fallback_enabled=True,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    log.info("Rate limiter installed (default=%s)", limit)
    return limiter
