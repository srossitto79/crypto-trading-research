from __future__ import annotations

from unittest.mock import patch

from axiom.agents.tools_research import (
    _tool_discover_github_repos,
    _tool_inspect_github_repo,
)


from tests.research_ext_utils import parse_tool_result as json_loads

def _all_pass_contract():
    return {
        "lane": "benchmarking",
        "external_sources_allowed": True,
        "allowed_external_source_types": ["github"],
    }


def _with_registry(monkeypatch, cfg):
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", lambda t: cfg)


def test_discover_blocked_when_github_not_allowed(monkeypatch):
    monkeypatch.setattr(
        "axiom.agents.tools_research._current_research_contract",
        lambda: {"lane": "benchmarking", "external_sources_allowed": True, "allowed_external_source_types": ["blog"]},
    )
    res = json_loads(_tool_discover_github_repos({"query": "x"}))
    assert res["ok"] is False
    assert "github" in res["error"]


def test_discover_blocked_when_registry_disabled(monkeypatch):
    _with_registry(monkeypatch, None)
    res = json_loads(_tool_discover_github_repos({"query": "x"}))
    assert res["ok"] is False
    assert "disabled" in res["error"]


def test_discover_happy_path(monkeypatch):
    _with_registry(monkeypatch, {"orgs": ["quantopian"], "rate_limit_per_min": 60, "personal_access_token": None})
    with patch("axiom.research_sources.github.search_github_repos") as m:
        m.return_value = {"ok": True, "source": "github", "query": "x", "count": 1, "results": [{"full_name": "a/b"}]}
        res = json_loads(_tool_discover_github_repos({"query": "x", "limit": 5}))
    assert res["ok"] is True
    assert m.call_args.kwargs["orgs"] == ["quantopian"]
    assert m.call_args.kwargs["limit"] == 5


def test_discover_passes_none_orgs_when_registry_empty(monkeypatch):
    _with_registry(monkeypatch, {"orgs": [], "rate_limit_per_min": 60, "personal_access_token": None})
    with patch("axiom.research_sources.github.search_github_repos") as m:
        m.return_value = {"ok": True, "source": "github", "query": "x", "count": 0, "results": []}
        json_loads(_tool_discover_github_repos({"query": "x"}))
    # With no orgs configured, search is unscoped (orgs=None or empty list; accept either)
    assert m.call_args.kwargs["orgs"] in (None, [])


def test_discover_params_orgs_override_registry(monkeypatch):
    _with_registry(monkeypatch, {"orgs": ["quantopian"], "rate_limit_per_min": 60, "personal_access_token": None})
    with patch("axiom.research_sources.github.search_github_repos") as m:
        m.return_value = {"ok": True, "source": "github", "query": "x", "count": 0, "results": []}
        json_loads(_tool_discover_github_repos({"query": "x", "orgs": ["hudson-and-thames"]}))
    assert m.call_args.kwargs["orgs"] == ["hudson-and-thames"]


def test_discover_pat_from_registry_passed_to_client(monkeypatch):
    _with_registry(monkeypatch, {"orgs": ["quantopian"], "rate_limit_per_min": 60, "personal_access_token": "ghp_xyz"})
    captured = {}
    with patch("axiom.research_sources.github._client") as cl, patch("axiom.research_sources.github.search_github_repos") as sr:
        cl.return_value = object()  # client marker
        sr.return_value = {"ok": True, "source": "github", "query": "x", "count": 0, "results": []}
        json_loads(_tool_discover_github_repos({"query": "x"}))
        captured["pat"] = cl.call_args.kwargs.get("pat")
    assert captured["pat"] == "ghp_xyz"


def test_discover_pat_none_when_empty_string(monkeypatch):
    _with_registry(monkeypatch, {"orgs": ["quantopian"], "rate_limit_per_min": 60, "personal_access_token": ""})
    with patch("axiom.research_sources.github._client") as cl, patch("axiom.research_sources.github.search_github_repos") as sr:
        cl.return_value = object()
        sr.return_value = {"ok": True, "source": "github", "query": "x", "count": 0, "results": []}
        json_loads(_tool_discover_github_repos({"query": "x"}))
        pat = cl.call_args.kwargs.get("pat")
    assert pat is None


def test_inspect_happy_path(monkeypatch):
    _with_registry(monkeypatch, {"orgs": ["quantopian"], "rate_limit_per_min": 60, "personal_access_token": None})
    with patch("axiom.research_sources.github.inspect_github_repo") as m:
        m.return_value = {"ok": True, "source": "github", "full_name": "a/b", "content": "body", "readme": "", "recent_issues": [], "metadata": {}}
        res = json_loads(_tool_inspect_github_repo({"full_name": "a/b"}))
    assert res["ok"] is True
    assert res["content"] == "body"


def test_inspect_blocked_when_not_benchmarking(monkeypatch):
    monkeypatch.setattr(
        "axiom.agents.tools_research._current_research_contract",
        lambda: {"lane": "exploration", "external_sources_allowed": True, "allowed_external_source_types": ["github"]},
    )
    res = json_loads(_tool_inspect_github_repo({"full_name": "a/b"}))
    assert res["ok"] is False


def test_inspect_registry_error(monkeypatch):
    from axiom.research_sources._registry import RegistryError
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    def _raise(_t): raise RegistryError("orgs malformed")
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", _raise)
    res = json_loads(_tool_inspect_github_repo({"full_name": "a/b"}))
    assert res["ok"] is False
    assert "github registry error" in res["error"]
