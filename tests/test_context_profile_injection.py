"""Phase 6 / P6-T02 — context-builder profile injection tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    from axiom import config

    monkeypatch.setattr(config, "WORKSPACE_DIR", ws, raising=False)
    monkeypatch.setattr(config, "LEGACY_WORKSPACE_DIR", ws, raising=False)

    # Patch the workspace module globals directly rather than importlib.reload:
    # reload rebinds Axiom.workspace.WorkspacePathError to a new class object,
    # which then leaks across the session and breaks unrelated tests that caught
    # the original class. The read_* helpers resolve WORKSPACE_DIR from the
    # module dict at call time, so monkeypatching the attributes is sufficient
    # and auto-restores on teardown.
    import axiom.workspace as ws_mod

    monkeypatch.setattr(ws_mod, "WORKSPACE_DIR", ws, raising=False)
    monkeypatch.setattr(ws_mod, "LEGACY_WORKSPACE_DIR", ws, raising=False)

    import axiom.context as ctx_mod

    yield ctx_mod, ws


def test_no_profile_returns_none(workspace):
    ctx_mod, _ = workspace
    assert ctx_mod._render_operator_profile() is None


def test_prose_only_user_md_renders_under_user_header(workspace):
    ctx_mod, ws = workspace
    (ws / "USER.md").write_text("Just some prose about me.\n", encoding="utf-8")
    rendered = ctx_mod._render_operator_profile()
    assert rendered is not None
    assert rendered.startswith("# USER")
    assert "Just some prose about me." in rendered


def test_structured_profile_renders_bullet_block(workspace):
    ctx_mod, ws = workspace
    (ws / "USER.md").write_text(
        """---
name: Trader
timezone: UTC
risk_per_trade_pct: 1.5
preferences:
  risk_appetite: conservative
  response_style: terse
rules:
  - "rule one"
  - "rule two"
---
extra body
""",
        encoding="utf-8",
    )
    rendered = ctx_mod._render_operator_profile()
    assert rendered is not None
    assert "# OPERATOR PROFILE" in rendered
    assert "- Name: Trader" in rendered
    assert "- Risk per trade: 1.5%" in rendered
    assert "- Risk appetite: conservative" in rendered
    assert "- Response style: terse" in rendered
    assert "  1. rule one" in rendered
    assert "  2. rule two" in rendered
    assert "extra body" in rendered


def test_empty_profile_object_returns_none(workspace):
    ctx_mod, ws = workspace
    (ws / "USER.md").write_text("---\n---\n", encoding="utf-8")
    rendered = ctx_mod._render_operator_profile()
    assert rendered is None
