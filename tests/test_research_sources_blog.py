from __future__ import annotations

from pathlib import Path

import httpx

from axiom.research_sources import blog

FIX = Path(__file__).parent / "fixtures"


def _mock(client, responses):
    it = iter(responses)

    def handler(req):
        return next(it)

    client._transport = httpx.MockTransport(handler)


def test_search_filters_by_keyword():
    body = (FIX / "blog_rss.xml").read_text()
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = blog.search_blog_articles("mean reversion", feeds=["https://example.com/feed"], limit=5, client=client)
    assert res["ok"] is True
    assert res["count"] >= 1
    assert all(
        "mean" in r["title"].lower()
        or "reversion" in (r.get("summary") or "").lower()
        or "mean" in (r.get("summary") or "").lower()
        for r in res["results"]
    )


def test_search_atom_feed_parses():
    body = (FIX / "blog_atom.xml").read_text()
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = blog.search_blog_articles("gap", feeds=["https://example.com/atom"], limit=5, client=client)
    assert res["ok"] is True
    assert res["count"] >= 1


def test_search_multi_feed_aggregates():
    rss = (FIX / "blog_rss.xml").read_text()
    atom = (FIX / "blog_atom.xml").read_text()
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=rss), httpx.Response(200, text=atom)])
    res = blog.search_blog_articles("mean", feeds=["https://example.com/rss", "https://example.com/atom"], limit=10, client=client)
    assert res["ok"] is True
    # At least one hit from RSS
    assert any(r["feed"] == "https://example.com/rss" for r in res["results"])


def test_search_404_feed_skipped_silently():
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(404)])
    res = blog.search_blog_articles("x", feeds=["https://dead.example/feed"], limit=5, client=client)
    assert res["ok"] is True
    assert res["count"] == 0


def test_search_empty_query():
    client = blog._client(rate_per_min=1000)
    res = blog.search_blog_articles("", feeds=["https://example.com/feed"], limit=5, client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_search_no_feeds():
    client = blog._client(rate_per_min=1000)
    res = blog.search_blog_articles("x", feeds=[], limit=5, client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_search_respects_limit():
    body = (FIX / "blog_rss.xml").read_text()
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = blog.search_blog_articles("strategy", feeds=["https://x/feed"], limit=1, client=client)
    # At least one fixture item has "strategy" in title/summary
    assert res["count"] <= 1


def test_inspect_extracts_plaintext():
    body = (FIX / "blog_article.html").read_text()
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = blog.inspect_blog_article("https://example.com/post", client=client)
    assert res["ok"] is True
    assert isinstance(res["content"], str)
    assert len(res["content"]) > 50
    # No HTML tags in content body
    assert "<p>" not in res["content"]
    assert "<nav>" not in res["content"]


def test_inspect_captures_title():
    body = (FIX / "blog_article.html").read_text()
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = blog.inspect_blog_article("https://example.com/post", client=client)
    assert res["ok"] is True
    # Title may come from <title>, <h1>, or metadata — accept any match
    assert "Mean Reversion" in (res.get("title") or "") or "Mean Reversion" in res["content"]


def test_inspect_empty_url():
    client = blog._client(rate_per_min=1000)
    res = blog.inspect_blog_article("", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_http_404_returns_error_code():
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(404), httpx.Response(404), httpx.Response(404)])
    res = blog.inspect_blog_article("https://example.com/missing", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "http_4xx"


def test_inspect_returns_empty_content_on_unextractable_html():
    minimal_html = "<html><body></body></html>"
    client = blog._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=minimal_html)])
    res = blog.inspect_blog_article("https://example.com/empty", client=client)
    assert res["ok"] is True
    assert res["content"] == ""
