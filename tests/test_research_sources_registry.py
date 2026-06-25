import pytest
from axiom.research_sources._registry import resolve_registry, RegistryError


def test_returns_none_for_disabled_source(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"reddit": {"enabled": False, "subs": ["algotrading"]}},
    )
    assert resolve_registry("reddit") is None


def test_returns_config_for_enabled_source(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"reddit": {"enabled": True, "subs": ["algotrading"], "rate_limit_per_min": 30,
                            "client_id": None, "client_secret": None}},
    )
    cfg = resolve_registry("reddit")
    assert cfg is not None
    assert cfg["subs"] == ["algotrading"]
    assert cfg["rate_limit_per_min"] == 30
    assert cfg["client_id"] is None
    assert cfg["client_secret"] is None


def test_raises_on_malformed_subs(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"reddit": {"enabled": True, "subs": "algotrading"}},
    )
    with pytest.raises(RegistryError):
        resolve_registry("reddit")


def test_unknown_source_returns_none_when_not_in_block(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {},
    )
    assert resolve_registry("reddit") is None


def test_unknown_source_type_enabled_raises(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"bogus": {"enabled": True, "things": []}},
    )
    with pytest.raises(RegistryError):
        resolve_registry("bogus")


def test_blog_registry_resolves(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"blog": {"enabled": True, "feeds": ["https://x/feed"], "rate_limit_per_min": 30}},
    )
    cfg = resolve_registry("blog")
    assert cfg == {"feeds": ["https://x/feed"], "rate_limit_per_min": 30}


def test_github_registry_resolves_with_pat(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"github": {"enabled": True, "orgs": ["quantopian"], "rate_limit_per_min": 60,
                             "personal_access_token": "ghp_x"}},
    )
    cfg = resolve_registry("github")
    assert cfg["orgs"] == ["quantopian"]
    assert cfg["personal_access_token"] == "ghp_x"


def test_forum_registry_resolves(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"forum": {"enabled": True, "sites": ["elitetrader.com"], "rate_limit_per_min": 20}},
    )
    cfg = resolve_registry("forum")
    assert cfg == {"sites": ["elitetrader.com"], "rate_limit_per_min": 20}


def test_zero_rate_limit_raises(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"reddit": {"enabled": True, "subs": ["algotrading"], "rate_limit_per_min": 0}},
    )
    with pytest.raises(RegistryError):
        resolve_registry("reddit")


def test_negative_rate_limit_raises(monkeypatch):
    monkeypatch.setattr(
        "axiom.research_sources._registry._load_settings_block",
        lambda: {"reddit": {"enabled": True, "subs": ["algotrading"], "rate_limit_per_min": -5}},
    )
    with pytest.raises(RegistryError):
        resolve_registry("reddit")
