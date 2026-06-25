import pytest
import httpx
from axiom.research_sources._http import (
    SourceHttpClient,
    RateLimitExceeded,
    UnsafeUrlError,
    _validate_public_url_static,
    MAX_RESPONSE_BYTES,
)


def test_rate_limiter_enforces_per_domain_budget():
    client = SourceHttpClient(default_rate_per_min=120, per_domain={"example.com": 2})
    with httpx.MockTransport(lambda req: httpx.Response(200, text="ok")) as transport:
        client._transport = transport  # test hook
        client.get("https://example.com/a")
        client.get("https://example.com/b")
        with pytest.raises(RateLimitExceeded):
            client.get("https://example.com/c")  # third call in same minute


def test_retries_on_5xx_with_backoff(monkeypatch):
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")
    client = SourceHttpClient(default_rate_per_min=1000)
    client._transport = httpx.MockTransport(handler)
    monkeypatch.setattr("time.sleep", lambda _: None)
    resp = client.get("https://example.com/x")
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_sets_user_agent_header():
    captured = {}
    def handler(req):
        captured["ua"] = req.headers.get("user-agent")
        return httpx.Response(200)
    client = SourceHttpClient(default_rate_per_min=1000)
    client._transport = httpx.MockTransport(handler)
    client.get("https://example.com/")
    assert "Axiom" in captured["ua"].lower()


def test_timeout_surfaces_as_error():
    def handler(req):
        raise httpx.ReadTimeout("slow")
    client = SourceHttpClient(default_rate_per_min=1000)
    client._transport = httpx.MockTransport(handler)
    with pytest.raises(httpx.ReadTimeout):
        client.get("https://example.com/")


def test_reuses_underlying_http_client():
    client = SourceHttpClient(default_rate_per_min=1000)
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, text="ok")
    client._transport = httpx.MockTransport(handler)
    client.get("https://example.com/a")
    c1 = client._client
    client.get("https://example.com/b")
    c2 = client._client
    assert c1 is c2 and c1 is not None
    client.close()


class TestSsrfGuard:
    """Static URL validation — the core SSRF defense. Checks IP literals
    and blocked hostnames without needing DNS, so tests stay hermetic."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://127.0.0.1:8003/api/ops/execution-mode",
            "http://localhost/",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",  # AWS metadata
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6 bypass
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/",
            "https://metadata.google.internal/",
        ],
    )
    def test_rejects_unsafe_url(self, url):
        with pytest.raises(UnsafeUrlError):
            _validate_public_url_static(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/",
            "https://reddit.com/r/algotrading.json",
            "http://github.com/user/repo",
        ],
    )
    def test_allows_public_url(self, url):
        # Static validator returns the hostname for safe URLs.
        host = _validate_public_url_static(url)
        assert host


def test_ssrf_rejects_redirect_to_loopback():
    """An attacker-hosted public URL that 302s to 127.0.0.1 must be refused."""
    def handler(req):
        return httpx.Response(
            302, headers={"Location": "http://127.0.0.1:8003/api/ops/execution-mode"}
        )
    client = SourceHttpClient(default_rate_per_min=1000)
    client._transport = httpx.MockTransport(handler)
    with pytest.raises(UnsafeUrlError):
        client.get("https://attacker.example.com/")


def test_ssrf_caps_response_body_size():
    big = b"x" * (MAX_RESPONSE_BYTES + 10)
    def handler(req):
        return httpx.Response(200, content=big)
    client = SourceHttpClient(default_rate_per_min=1000)
    client._transport = httpx.MockTransport(handler)
    with pytest.raises(UnsafeUrlError, match="byte limit"):
        client.get("https://example.com/huge")
