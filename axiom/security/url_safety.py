"""SSRF / URL-safety guard.

Centralized canonical implementation of the URL-blocklist used by every
HTTP-fetching subsystem in Axiom. Originally lived in
``Axiom/research_sources/_http.py`` (Phase 4 security audit, 2026-04-23);
extracted here in Phase 5 / P5-T02 so the same policy gates research source
fetches AND MCP HTTP transports AND any future external-URL ingest paths.

Public API:
- ``UnsafeUrlError`` — raised on any blocked URL.
- ``is_blocked_ip(ip: str) -> bool`` — resolves loopback / RFC1918 / link-local
  / multicast / reserved / unspecified, including IPv4-mapped IPv6.
- ``validate_public_url(url, *, skip_dns=False)`` — full guard with DNS pin
  protection (every A/AAAA record must be public).
- ``validate_public_url_static(url) -> str`` — cheap static-only check that
  returns the lowercased hostname; useful when the caller wants to gate
  on hostname format without paying DNS.

Cloud metadata endpoints explicitly covered:
- AWS / GCP / Azure: ``169.254.169.254`` and ``fd00:ec2::254`` (link-local
  v4 + v6) — caught by ``is_blocked_ip`` since both are link-local.
- ``metadata.google.internal``, ``metadata.goog`` — caught by hostname blocklist.

This module deliberately has zero deps beyond the stdlib so any subsystem
can import it without dragging httpx into its closure.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_FORBIDDEN_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
})


class UnsafeUrlError(RuntimeError):
    """Raised when a URL resolves to a private/loopback/metadata address."""


def is_blocked_ip(ip: str) -> bool:
    """True if ``ip`` is loopback, private, link-local, multicast, or reserved.

    Covers IPv4 *and* IPv6 — and IPv4-mapped IPv6 (``::ffff:127.0.0.1``) which
    is the classic SSRF bypass. Unparseable strings are treated as unsafe.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_public_url_static(url: str) -> str:
    """Cheap checks: scheme + hostname format + explicit hostname blocklist.

    Returns the (lowercased) hostname for reuse. Raises ``UnsafeUrlError``
    for anything wrong that can be determined without touching the network.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"scheme not allowed: {parsed.scheme!r} in {url!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeUrlError(f"no hostname in {url!r}")
    if host in _FORBIDDEN_HOSTNAMES:
        raise UnsafeUrlError(f"hostname {host!r} is forbidden")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and is_blocked_ip(str(literal)):
        raise UnsafeUrlError(f"URL points at blocked address {host!r}")
    return host


def validate_public_url_dns(host: str) -> None:
    """Resolve ``host`` and reject if ANY A/AAAA record lands on a blocked IP.

    Defense against DNS-pinning: an attacker-controlled domain can have ONE
    public record + ONE RFC1918 record. We refuse the whole fetch if any
    resolved address is blocked.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"DNS lookup failed for {host!r}: {exc}") from exc
    for info in infos:
        ip = info[4][0]
        if is_blocked_ip(ip):
            raise UnsafeUrlError(
                f"hostname {host!r} resolves to blocked address {ip!r}"
            )


def validate_public_url(url: str, *, skip_dns: bool = False) -> None:
    """Full SSRF guard: static checks + DNS resolution.

    ``skip_dns=True`` is for test harnesses that stub out the HTTP transport
    entirely — no network call happens, so resolving the hostname adds
    nothing and just fails on hosts like ``example.com`` when the runner is
    offline. Production callers never pass this.
    """
    host = validate_public_url_static(url)
    if not skip_dns:
        validate_public_url_dns(host)


# Underscore aliases retained for legacy callers in research_sources/_http.py.
_is_blocked_ip = is_blocked_ip
_validate_public_url_static = validate_public_url_static
_validate_public_url_dns = validate_public_url_dns
_validate_public_url = validate_public_url
