"""Regression tests for H-S6 (HTTP rate limiting via slowapi)."""

from __future__ import annotations



from axiom import rate_limiting


def test_rate_limit_enabled_default(monkeypatch):
    monkeypatch.delenv("AXIOM_RATE_LIMIT_ENABLED", raising=False)
    assert rate_limiting.rate_limit_enabled() is True


def test_rate_limit_disabled_via_env(monkeypatch):
    monkeypatch.setenv("AXIOM_RATE_LIMIT_ENABLED", "0")
    assert rate_limiting.rate_limit_enabled() is False


def test_default_limit_overridable_via_env(monkeypatch):
    monkeypatch.setenv("AXIOM_RATE_LIMIT_DEFAULT", "5/second")
    assert rate_limiting.get_default_limit() == "5/second"


def test_health_paths_are_exempt():
    assert rate_limiting.is_exempt_path("/api/health")
    assert rate_limiting.is_exempt_path("/api/health/live")
    assert rate_limiting.is_exempt_path("/api/status")
    assert rate_limiting.is_exempt_path("/api/ws/topic")
    assert rate_limiting.is_exempt_path("/healthz")


def test_other_paths_are_not_exempt():
    assert not rate_limiting.is_exempt_path("/api/strategies")
    assert not rate_limiting.is_exempt_path("/api/agents/run")
    assert not rate_limiting.is_exempt_path("")


def test_install_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("AXIOM_RATE_LIMIT_ENABLED", "0")

    class _FakeApp:
        state = type("_S", (), {})()

        def add_exception_handler(self, *a, **kw): pass
        def add_middleware(self, *a, **kw): pass

    assert rate_limiting.install_rate_limiter(_FakeApp()) is None


def test_install_returns_limiter_when_enabled(monkeypatch):
    monkeypatch.setenv("AXIOM_RATE_LIMIT_ENABLED", "1")

    class _FakeApp:
        state = type("_S", (), {})()

        def __init__(self):
            self.handlers = []
            self.middlewares = []

        def add_exception_handler(self, exc, h):
            self.handlers.append((exc, h))

        def add_middleware(self, mw, **kw):
            self.middlewares.append(mw)

    app = _FakeApp()
    limiter = rate_limiting.install_rate_limiter(app)
    # If slowapi is installed (it is — required dep), we should get a Limiter.
    # If not, this test would correctly skip via None — but slowapi is in deps.
    assert limiter is not None
    assert app.handlers, "exception handler should be registered"
    assert app.middlewares, "middleware should be registered"
