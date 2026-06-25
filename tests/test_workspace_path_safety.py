"""Regression tests for H-S8 (workspace path traversal + symlink resistance)."""

from __future__ import annotations

import os
import pytest

from axiom.workspace import WorkspacePathError, safe_workspace_path


def test_simple_relative_path_resolves(tmp_path):
    out = safe_workspace_path("LESSONS.md", root=tmp_path)
    assert out.name == "LESSONS.md"
    assert out.parent == tmp_path.resolve()


def test_subdir_path_resolves(tmp_path):
    (tmp_path / "memory").mkdir()
    out = safe_workspace_path("memory/2026-01-01.md", root=tmp_path)
    assert out.parent.name == "memory"


def test_absolute_path_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("/etc/passwd", root=tmp_path)


def test_windows_drive_path_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("C:\\Windows\\notepad.exe", root=tmp_path)


def test_traversal_double_dot_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("../etc/passwd", root=tmp_path)


def test_traversal_nested_double_dot_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("memory/../../secret.md", root=tmp_path)


def test_backslash_traversal_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("..\\..\\Windows\\boot.ini", root=tmp_path)


def test_empty_path_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("", root=tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires admin on Windows")
def test_symlink_escaping_workspace_rejected(tmp_path):
    """A subdir is a symlink pointing OUTSIDE the workspace; rejected."""
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "evil_link").symlink_to(outside)
    with pytest.raises(WorkspacePathError):
        safe_workspace_path("evil_link/secret.md", root=workspace)


def test_path_inside_workspace_passes_even_through_subdirs(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "sub").mkdir()
    out = safe_workspace_path("sub/deeper/file.md", root=workspace)
    assert workspace.resolve() in out.resolve().parents or out.parent == workspace.resolve() or workspace.resolve() in out.parents
