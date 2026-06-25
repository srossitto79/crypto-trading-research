"""Phase 5 / P5-T02 — SSRF guard at ``Axiom/security/url_safety.py``.

The blocklist used to live in ``Axiom/research_sources/_http.py``; Phase 5
extracted it so MCP HTTP transport (P5-T02b) and any future external-fetch
path can call the same canonical guard. Tests cover:

* IPv4 / IPv6 / IPv4-mapped IPv6 blocking semantics
* Cloud metadata endpoints (AWS / GCP / Azure)
* Hostname blocklist + scheme rejection
* DNS-pin protection (any resolved A record must be public)
* Backwards-compatibility re-exports from research_sources/_http.py
"""
from __future__ import annotations

import socket
from unittest import mock

import pytest

from axiom.security.url_safety import (
    UnsafeUrlError,
    is_blocked_ip,
    validate_public_url,
    validate_public_url_static,
)


# --- is_blocked_ip --------------------------------------------------------

class TestIsBlockedIp:
    def test_loopback_v4_blocked(self) -> None:
        assert is_blocked_ip("127.0.0.1") is True

    def test_loopback_v6_blocked(self) -> None:
        assert is_blocked_ip("::1") is True

    def test_rfc1918_blocked(self) -> None:
        assert is_blocked_ip("10.0.0.1") is True
        assert is_blocked_ip("172.16.0.1") is True
        assert is_blocked_ip("192.168.1.1") is True

    def test_link_local_blocked(self) -> None:
        # AWS / GCP IMDSv2 endpoint — must always be blocked.
        assert is_blocked_ip("169.254.169.254") is True

    def test_ipv4_mapped_v6_blocked(self) -> None:
        """Classic SSRF bypass: ``::ffff:127.0.0.1`` is loopback in disguise."""
        assert is_blocked_ip("::ffff:127.0.0.1") is True
        assert is_blocked_ip("::ffff:10.0.0.1") is True

    def test_multicast_blocked(self) -> None:
        assert is_blocked_ip("224.0.0.1") is True

    def test_unspecified_blocked(self) -> None:
        assert is_blocked_ip("0.0.0.0") is True

    def test_garbage_string_blocked(self) -> None:
        # Unparseable input is treated as unsafe — defense in depth.
        assert is_blocked_ip("not-an-ip") is True
        assert is_blocked_ip("") is True

    def test_public_ip_allowed(self) -> None:
        assert is_blocked_ip("8.8.8.8") is False
        assert is_blocked_ip("1.1.1.1") is False

    def test_public_v6_allowed(self) -> None:
        # Cloudflare DNS over IPv6 — public.
        assert is_blocked_ip("2606:4700:4700::1111") is False


# --- validate_public_url_static -------------------------------------------

class TestValidatePublicUrlStatic:
    def test_rejects_localhost_hostname(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("http://localhost/x")

    def test_rejects_metadata_internal(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("http://metadata.google.internal/")

    def test_rejects_metadata_goog(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("http://metadata.goog/")

    def test_rejects_imds_literal_v4(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("http://169.254.169.254/latest/meta-data/")

    def test_rejects_loopback_literal(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("http://127.0.0.1:8080/")

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("file:///etc/passwd")
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("ftp://example.com/")
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("gopher://example.com/")

    def test_rejects_no_hostname(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_public_url_static("http:///nopath")

    def test_returns_lowercased_hostname(self) -> None:
        host = validate_public_url_static("http://EXAMPLE.com/path")
        assert host == "example.com"

    def test_allows_public_https(self) -> None:
        # No exception, returns hostname.
        host = validate_public_url_static("https://example.com/api")
        assert host == "example.com"


# --- validate_public_url (with DNS) ---------------------------------------

class TestValidatePublicUrlWithDns:
    def test_rejects_when_any_resolved_ip_is_blocked(self) -> None:
        """DNS-pinning defense: domain that resolves to RFC1918 is rejected."""
        fake = [(socket.AF_INET, None, None, "", ("10.0.0.7", 0))]
        with mock.patch("axiom.security.url_safety.socket.getaddrinfo", return_value=fake):
            with pytest.raises(UnsafeUrlError):
                validate_public_url("https://attacker.example/")

    def test_rejects_when_one_of_many_records_blocked(self) -> None:
        """If A1 is public and A2 is private, refuse the whole fetch."""
        fake = [
            (socket.AF_INET, None, None, "", ("8.8.8.8", 0)),
            (socket.AF_INET, None, None, "", ("169.254.169.254", 0)),
        ]
        with mock.patch("axiom.security.url_safety.socket.getaddrinfo", return_value=fake):
            with pytest.raises(UnsafeUrlError):
                validate_public_url("https://mixed.example/")

    def test_passes_when_all_records_public(self) -> None:
        fake = [
            (socket.AF_INET, None, None, "", ("8.8.8.8", 0)),
            (socket.AF_INET, None, None, "", ("1.1.1.1", 0)),
        ]
        with mock.patch("axiom.security.url_safety.socket.getaddrinfo", return_value=fake):
            validate_public_url("https://example.com/")  # no exception

    def test_dns_failure_raises(self) -> None:
        with mock.patch(
            "axiom.security.url_safety.socket.getaddrinfo",
            side_effect=socket.gaierror("nodename nor servname"),
        ):
            with pytest.raises(UnsafeUrlError):
                validate_public_url("https://nonexistent.example/")

    def test_skip_dns_bypasses_resolution(self) -> None:
        """``skip_dns=True`` is for test harnesses that stub HTTP entirely."""
        with mock.patch(
            "axiom.security.url_safety.socket.getaddrinfo",
            side_effect=AssertionError("DNS should not be called"),
        ):
            validate_public_url("https://example.com/", skip_dns=True)

    def test_skip_dns_still_runs_static_checks(self) -> None:
        """``skip_dns=True`` does NOT disable static blocklist."""
        with pytest.raises(UnsafeUrlError):
            validate_public_url("http://localhost/", skip_dns=True)


# --- backwards-compat from research_sources/_http -------------------------

def test_research_sources_http_reexports_unsafe_url_error() -> None:
    """Legacy callers in research_sources/_http.py still see the same symbol."""
    from axiom.research_sources._http import UnsafeUrlError as Legacy
    assert Legacy is UnsafeUrlError


def test_underscore_aliases_present() -> None:
    """Underscored aliases retained for ``research_sources/_http`` callers."""
    from axiom.security import url_safety as us
    assert us._is_blocked_ip is is_blocked_ip
    assert us._validate_public_url is validate_public_url
    assert us._validate_public_url_static is validate_public_url_static
