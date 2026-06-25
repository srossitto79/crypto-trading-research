"""Regression tests for H-S7 (CSV upload size + content-type bound)."""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from axiom.routers.data import _read_upload_bounded, _max_upload_bytes


def _make_upload(content: bytes, *, content_type: str = "text/csv", filename: str = "data.csv") -> UploadFile:
    headers = Headers({"content-type": content_type})
    return UploadFile(filename=filename, file=BytesIO(content), headers=headers)


def test_max_upload_bytes_default(monkeypatch):
    monkeypatch.delenv("AXIOM_MAX_UPLOAD_BYTES", raising=False)
    assert _max_upload_bytes() == 50 * 1024 * 1024


def test_max_upload_bytes_overridable(monkeypatch):
    monkeypatch.setenv("AXIOM_MAX_UPLOAD_BYTES", "1024")
    assert _max_upload_bytes() == 1024


def test_max_upload_bytes_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("AXIOM_MAX_UPLOAD_BYTES", "not-a-number")
    assert _max_upload_bytes() == 50 * 1024 * 1024


def test_small_csv_upload_succeeds(monkeypatch):
    upload = _make_upload(b"a,b,c\n1,2,3\n", content_type="text/csv")
    out = asyncio.run(_read_upload_bounded(upload, max_bytes=1024))
    assert out == b"a,b,c\n1,2,3\n"


def test_oversize_upload_rejected(monkeypatch):
    huge = b"x" * 4096
    upload = _make_upload(huge, content_type="text/csv")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_upload_bounded(upload, max_bytes=1024))
    assert exc.value.status_code == 413


def test_unsupported_content_type_rejected():
    upload = _make_upload(b"<html></html>", content_type="text/html", filename="evil.csv")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_upload_bounded(upload))
    assert exc.value.status_code == 415


def test_unsupported_extension_rejected():
    upload = _make_upload(b"echo pwned", content_type="text/csv", filename="evil.exe")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_upload_bounded(upload))
    assert exc.value.status_code == 415


def test_extension_inferred_from_filename_only():
    """No content-type header, valid CSV extension — should pass."""
    headers = Headers({})
    upload = UploadFile(filename="data.csv", file=BytesIO(b"a,b\n1,2\n"), headers=headers)
    out = asyncio.run(_read_upload_bounded(upload, max_bytes=1024))
    assert out == b"a,b\n1,2\n"
