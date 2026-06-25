from __future__ import annotations

from unittest.mock import patch

from axiom.agents.tools_research import (
    _tool_discover_forum_threads,
    _tool_inspect_forum_thread,
)


from tests.research_ext_utils import parse_tool_result as json_loads

def _all_pass_contract():
    return {
        "lane": "benchmarking",
        "external_sources_allowed": True,
        "allowed_external_source_types": ["forum"],
    }


def _with_registry(monkeypatch, cfg):
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", lambda t: cfg)


def test_discover_blocked_when_forum_not_allowed(monkeypatch):
    monkeypatch.setattr(
        "axiom.agents.tools_research._current_research_contract",
        lambda: {"lane": "benchmarking", "external_sources_allowed": True, "allowed_external_source_types": ["blog"]},
    )
    res = json_loads(_tool_discover_forum_threads({"query": "x"}))
    assert res["ok"] is False
    assert "forum" in res["error"]


def test_discover_blocked_when_registry_disabled(monkeypatch):
    _with_registry(monkeypatch, None)
    res = json_loads(_tool_discover_forum_threads({"query": "x"}))
    assert res["ok"] is False
    assert "disabled" in res["error"]


def test_discover_blocked_when_no_sites(monkeypatch):
    _with_registry(monkeypatch, {"sites": [], "rate_limit_per_min": 20})
    res = json_loads(_tool_discover_forum_threads({"query": "x"}))
    assert res["ok"] is False
    assert "no sites" in res["error"]


def test_discover_happy_path(monkeypatch):
    _with_registry(monkeypatch, {"sites": ["elitetrader.com"], "rate_limit_per_min": 20})
    with patch("axiom.research_sources.forum.search_forum_threads") as m:
        m.return_value = {"ok": True, "source": "forum", "query": "x", "count": 1, "results": [{"url": "u"}]}
        res = json_loads(_tool_discover_forum_threads({"query": "x", "limit": 5}))
    assert res["ok"] is True
    assert m.call_args.kwargs["sites"] == ["elitetrader.com"]
    assert m.call_args.kwargs["limit"] == 5


def test_discover_sites_override_registry(monkeypatch):
    _with_registry(monkeypatch, {"sites": ["elitetrader.com"], "rate_limit_per_min": 20})
    with patch("axiom.research_sources.forum.search_forum_threads") as m:
        m.return_value = {"ok": True, "source": "forum", "query": "x", "count": 0, "results": []}
        json_loads(_tool_discover_forum_threads({"query": "x", "sites": ["quantconnect.com"]}))
    assert m.call_args.kwargs["sites"] == ["quantconnect.com"]


def test_discover_registry_error(monkeypatch):
    from axiom.research_sources._registry import RegistryError
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    def _raise(_t): raise RegistryError("sites malformed")
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", _raise)
    res = json_loads(_tool_discover_forum_threads({"query": "x"}))
    assert res["ok"] is False
    assert "forum registry error" in res["error"]


def test_inspect_happy_path(monkeypatch):
    _with_registry(monkeypatch, {"sites": ["elitetrader.com"], "rate_limit_per_min": 20})
    with patch("axiom.research_sources.forum.inspect_forum_thread") as m:
        m.return_value = {"ok": True, "source": "forum", "url": "u", "title": "T", "posts": [], "content": "body", "site": "elitetrader.com"}
        res = json_loads(_tool_inspect_forum_thread({"url": "https://www.elitetrader.com/et/threads/x.1/"}))
    assert res["ok"] is True
    assert res["content"] == "body"


def test_inspect_blocked_when_not_benchmarking(monkeypatch):
    monkeypatch.setattr(
        "axiom.agents.tools_research._current_research_contract",
        lambda: {"lane": "exploration", "external_sources_allowed": True, "allowed_external_source_types": ["forum"]},
    )
    res = json_loads(_tool_inspect_forum_thread({"url": "https://x"}))
    assert res["ok"] is False


def test_inspect_registry_error(monkeypatch):
    from axiom.research_sources._registry import RegistryError
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    def _raise(_t): raise RegistryError("sites malformed")
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", _raise)
    res = json_loads(_tool_inspect_forum_thread({"url": "https://x"}))
    assert res["ok"] is False
    assert "forum registry error" in res["error"]
