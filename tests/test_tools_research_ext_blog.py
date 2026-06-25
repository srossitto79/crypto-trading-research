from __future__ import annotations

from unittest.mock import patch

from axiom.agents.tools_research import (
    _tool_discover_blog_articles,
    _tool_inspect_blog_article,
)

from tests.research_ext_utils import parse_tool_result as json_loads


def _all_pass_contract():
    return {
        "lane": "benchmarking",
        "external_sources_allowed": True,
        "allowed_external_source_types": ["blog"],
    }


def _with_registry(monkeypatch, cfg):
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", lambda t: cfg)


def test_discover_blocked_when_blog_not_allowed(monkeypatch):
    monkeypatch.setattr(
        "axiom.agents.tools_research._current_research_contract",
        lambda: {"lane": "benchmarking", "external_sources_allowed": True, "allowed_external_source_types": ["youtube"]},
    )
    res = json_loads(_tool_discover_blog_articles({"query": "x"}))
    assert res["ok"] is False
    assert "blog" in res["error"]


def test_discover_blocked_when_registry_disabled(monkeypatch):
    _with_registry(monkeypatch, None)
    res = json_loads(_tool_discover_blog_articles({"query": "x"}))
    assert res["ok"] is False
    assert "disabled" in res["error"]


def test_discover_blocked_when_no_feeds(monkeypatch):
    _with_registry(monkeypatch, {"feeds": [], "rate_limit_per_min": 30})
    res = json_loads(_tool_discover_blog_articles({"query": "x"}))
    assert res["ok"] is False
    assert "no feeds" in res["error"]


def test_discover_happy_path(monkeypatch):
    _with_registry(monkeypatch, {"feeds": ["https://x/feed"], "rate_limit_per_min": 30})
    with patch("axiom.research_sources.blog.search_blog_articles") as m:
        m.return_value = {"ok": True, "source": "blog", "query": "x", "count": 1, "results": [{"title": "a"}]}
        res = json_loads(_tool_discover_blog_articles({"query": "x", "limit": 5}))
    assert res["ok"] is True
    assert m.call_args.kwargs["feeds"] == ["https://x/feed"]
    assert m.call_args.kwargs["limit"] == 5


def test_discover_feeds_override_registry(monkeypatch):
    _with_registry(monkeypatch, {"feeds": ["https://a/feed"], "rate_limit_per_min": 30})
    with patch("axiom.research_sources.blog.search_blog_articles") as m:
        m.return_value = {"ok": True, "source": "blog", "query": "x", "count": 0, "results": []}
        json_loads(_tool_discover_blog_articles({"query": "x", "feeds": ["https://b/feed"]}))
    assert m.call_args.kwargs["feeds"] == ["https://b/feed"]


def test_discover_registry_error(monkeypatch):
    from axiom.research_sources._registry import RegistryError
    monkeypatch.setattr("axiom.agents.tools_research._current_research_contract", _all_pass_contract)
    def _raise(_t): raise RegistryError("feeds must be list[str]")
    monkeypatch.setattr("axiom.agents.tools_research._resolve_source_registry", _raise)
    res = json_loads(_tool_discover_blog_articles({"query": "x"}))
    assert res["ok"] is False
    assert "blog registry error" in res["error"]


def test_inspect_happy_path(monkeypatch):
    _with_registry(monkeypatch, {"feeds": ["https://x/feed"], "rate_limit_per_min": 30})
    with patch("axiom.research_sources.blog.inspect_blog_article") as m:
        m.return_value = {"ok": True, "source": "blog", "url": "u", "title": "T", "content": "body"}
        res = json_loads(_tool_inspect_blog_article({"url": "https://example.com/a"}))
    assert res["ok"] is True
    assert res["content"] == "body"


def test_inspect_blocked_when_not_benchmarking(monkeypatch):
    monkeypatch.setattr(
        "axiom.agents.tools_research._current_research_contract",
        lambda: {"lane": "exploration", "external_sources_allowed": True, "allowed_external_source_types": ["blog"]},
    )
    res = json_loads(_tool_inspect_blog_article({"url": "https://x"}))
    assert res["ok"] is False


def test_inspect_blocked_when_registry_disabled(monkeypatch):
    _with_registry(monkeypatch, None)
    res = json_loads(_tool_inspect_blog_article({"url": "https://x"}))
    assert res["ok"] is False
    assert "disabled" in res["error"]
