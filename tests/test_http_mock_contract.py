"""H-T2: HTTP mock-vs-prod contract checks.

The project mocks httpx responses in many tests with MagicMock and custom
objects. When the mocks drift from the actual httpx.Response interface,
tests pass while production code breaks (e.g., mock returns `.json()` as a
dict but real httpx returns a callable). These tests pin down the
interface shape so that breaking changes in our mocks surface early.
"""

from __future__ import annotations

import httpx
import pytest


def test_httpx_response_surface_matches_our_mock_assumptions():
    """A real httpx.Response exposes the attributes our code relies on."""
    req = httpx.Request("GET", "https://example.test/")
    resp = httpx.Response(200, request=req, json={"k": 1})
    # Attributes we depend on across Axiom/*.py:
    assert hasattr(resp, "status_code")
    assert hasattr(resp, "json")
    assert hasattr(resp, "raise_for_status")
    assert hasattr(resp, "text")
    assert hasattr(resp, "headers")

    # .json() must be callable and return the decoded body
    assert callable(resp.json)
    assert resp.json() == {"k": 1}

    # .status_code is an int
    assert isinstance(resp.status_code, int)


def test_httpx_raise_for_status_raises_on_4xx():
    """raise_for_status on a 4xx raises httpx.HTTPStatusError."""
    req = httpx.Request("GET", "https://example.test/")
    resp = httpx.Response(404, request=req)
    with pytest.raises(httpx.HTTPStatusError):
        resp.raise_for_status()


def test_httpx_raise_for_status_ok_on_2xx():
    req = httpx.Request("GET", "https://example.test/")
    resp = httpx.Response(200, request=req)
    # Should not raise.
    resp.raise_for_status()


def test_httpx_request_error_is_subclass_of_base_error():
    """Our code catches httpx.HTTPError as the broad fallback — assert the
    inheritance so a future httpx version that separates TimeoutException
    from HTTPError would break this test before breaking production."""
    assert issubclass(httpx.HTTPStatusError, httpx.HTTPError)
    assert issubclass(httpx.RequestError, httpx.HTTPError)
    assert issubclass(httpx.TimeoutException, httpx.HTTPError)


def test_httpx_timeout_default_not_none():
    """When we construct a Client without explicit timeout, httpx must
    apply a real default so requests don't hang forever."""
    client = httpx.Client()
    try:
        # Newer httpx exposes .timeout on the client; it should not be
        # None by default.
        assert client.timeout is not None
    finally:
        client.close()


def test_httpx_client_accepts_timeout_param():
    """Our code passes timeout=30 — confirm the Client accepts this."""
    client = httpx.Client(timeout=30.0)
    try:
        assert client.timeout is not None
    finally:
        client.close()
