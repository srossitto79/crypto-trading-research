"""Phase 6 / P6-T01 — operator profile parser tests."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point WORKSPACE_DIR at a tmp dir for the test."""
    ws = tmp_path / "ws"
    ws.mkdir()
    from axiom import config

    monkeypatch.setattr(config, "WORKSPACE_DIR", ws, raising=False)
    monkeypatch.setattr(config, "LEGACY_WORKSPACE_DIR", ws, raising=False)

    import axiom.workspace as ws_mod

    importlib.reload(ws_mod)
    monkeypatch.setattr(ws_mod, "WORKSPACE_DIR", ws, raising=False)
    monkeypatch.setattr(ws_mod, "LEGACY_WORKSPACE_DIR", ws, raising=False)
    yield ws_mod, ws
    importlib.reload(ws_mod)


def test_returns_none_when_user_md_missing(workspace):
    ws_mod, _ = workspace
    assert ws_mod.read_operator_profile() is None


def test_parses_full_profile(workspace):
    ws_mod, ws = workspace
    (ws / "USER.md").write_text(
        """---
name: Trader
timezone: America/Chicago
starting_capital_usd: 10000
risk_per_trade_pct: 1.5
exchange: hyperliquid
asset_universe: crypto
preferences:
  notification_channels: [in-app]
  quiet_hours: "22:00-08:00"
  risk_appetite: conservative
  response_style: terse
rules:
  - "no strategy goes live without backtest"
  - "max 1 concurrent live deployment per market regime"
---
Free-form notes here.
""",
        encoding="utf-8",
    )

    p = ws_mod.read_operator_profile()
    assert p is not None
    assert p.name == "Trader"
    assert p.timezone == "America/Chicago"
    assert p.starting_capital_usd == 10000.0
    assert p.risk_per_trade_pct == 1.5
    assert p.exchange == "hyperliquid"
    assert p.asset_universe == "crypto"
    assert p.preferences.notification_channels == ["in-app"]
    assert p.preferences.quiet_hours == "22:00-08:00"
    assert p.preferences.risk_appetite == "conservative"
    assert p.preferences.response_style == "terse"
    assert len(p.rules) == 2
    assert "Free-form notes" in p.body
    assert p.parse_error is None
    assert p.has_structured


def test_body_only_no_frontmatter(workspace):
    ws_mod, ws = workspace
    (ws / "USER.md").write_text("Just prose, no YAML.\n", encoding="utf-8")
    p = ws_mod.read_operator_profile()
    assert p is not None
    assert p.body.strip() == "Just prose, no YAML."
    assert p.name is None
    assert not p.has_structured


def test_malformed_frontmatter_returns_body_only(workspace):
    ws_mod, ws = workspace
    (ws / "USER.md").write_text(
        "---\nname: : : broken\n---\nhello\n",
        encoding="utf-8",
    )
    p = ws_mod.read_operator_profile()
    assert p is not None
    assert p.parse_error is not None
    assert p.body.strip() == "hello"


def test_invalid_enum_values_become_none(workspace):
    ws_mod, ws = workspace
    (ws / "USER.md").write_text(
        """---
preferences:
  risk_appetite: reckless
  response_style: shouting
---
""",
        encoding="utf-8",
    )
    p = ws_mod.read_operator_profile()
    assert p is not None
    assert p.preferences.risk_appetite is None
    assert p.preferences.response_style is None


def test_round_trip_write_read(workspace):
    ws_mod, _ = workspace
    profile = ws_mod.OperatorProfile(
        name="Trader",
        timezone="UTC",
        risk_per_trade_pct=1.0,
        preferences=ws_mod.OperatorPreferences(
            risk_appetite="balanced",
            response_style="terse",
        ),
        rules=["rule one", "rule two"],
        body="Some prose.",
    )
    ws_mod.write_operator_profile(profile)
    reloaded = ws_mod.read_operator_profile()
    assert reloaded is not None
    assert reloaded.name == "Trader"
    assert reloaded.timezone == "UTC"
    assert reloaded.risk_per_trade_pct == 1.0
    assert reloaded.preferences.risk_appetite == "balanced"
    assert reloaded.preferences.response_style == "terse"
    assert reloaded.rules == ["rule one", "rule two"]
    assert "Some prose." in reloaded.body


def test_body_only_profile_roundtrips_without_frontmatter(workspace):
    ws_mod, ws = workspace
    profile = ws_mod.OperatorProfile(body="prose only")
    ws_mod.write_operator_profile(profile)
    raw = (ws / "USER.md").read_text(encoding="utf-8")
    assert not raw.lstrip().startswith("---")
    assert "prose only" in raw
