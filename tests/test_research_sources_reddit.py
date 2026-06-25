from __future__ import annotations

from pathlib import Path

import httpx

from axiom.research_sources import reddit

FIX = Path(__file__).parent / "fixtures"


def _mock(client, responses):
    it = iter(responses)

    def handler(req):
        return next(it)

    client._transport = httpx.MockTransport(handler)


def test_search_parses_posts():
    body = (FIX / "reddit_search.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = reddit.search_reddit_posts("mean reversion", subs=["algotrading"], limit=5, client=client)
    assert res["ok"] is True
    assert res["count"] >= 1
    first = res["results"][0]
    assert {"permalink", "title", "subreddit", "score", "num_comments", "created_utc"} <= first.keys()
    assert first["title"] == "Mean reversion on stretched funding rates"


def test_search_aggregates_across_subs():
    body = (FIX / "reddit_search.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body), httpx.Response(200, text=body)])
    res = reddit.search_reddit_posts("x", subs=["algotrading", "quant"], limit=10, client=client)
    assert res["ok"] is True
    assert res["count"] <= 10


def test_search_respects_limit():
    body = (FIX / "reddit_search.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = reddit.search_reddit_posts("x", subs=["algotrading"], limit=1, client=client)
    assert res["count"] == 1


def test_search_empty_query():
    client = reddit._client(rate_per_min=1000)
    res = reddit.search_reddit_posts("", subs=["algotrading"], limit=5, client=client)
    assert res["ok"] is False
    assert "empty query" in res["error"]
    assert res["error_code"] == "invalid_input"


def test_search_no_subs():
    client = reddit._client(rate_per_min=1000)
    res = reddit.search_reddit_posts("x", subs=[], limit=5, client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_search_rate_limit_429_returns_error_code():
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(429, text='{"error":429}'), httpx.Response(429, text='{"error":429}'), httpx.Response(429, text='{"error":429}')])
    res = reddit.search_reddit_posts("x", subs=["algotrading"], limit=5, client=client)
    assert res["ok"] is False
    assert res["error_code"] == "rate_limited"


def test_search_http_500_returns_error_code():
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(500), httpx.Response(500), httpx.Response(500)])
    res = reddit.search_reddit_posts("x", subs=["algotrading"], limit=5, client=client)
    assert res["ok"] is False
    assert res["error_code"] == "http_5xx"


def test_inspect_returns_content_with_title_and_comments():
    body = (FIX / "reddit_thread.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = reddit.inspect_reddit_thread("/r/algotrading/comments/abc123/", client=client)
    assert res["ok"] is True
    assert "Mean reversion on stretched funding rates" in res["content"]
    assert "mean-reversion strategy on BTC" in res["content"]
    assert any("ADX filtering" in c for c in res["top_comments"])
    assert res["url"].endswith(".json")


def test_inspect_walks_nested_replies():
    body = (FIX / "reddit_thread.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = reddit.inspect_reddit_thread("/r/algotrading/comments/abc123/", client=client)
    # Nested reply should be captured
    assert any("Cut drawdown" in c for c in res["top_comments"])


def test_inspect_deleted_thread_is_ok():
    body = (FIX / "reddit_deleted.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = reddit.inspect_reddit_thread("/r/algotrading/comments/del999/", client=client)
    assert res["ok"] is True
    assert res["selftext"] == "[deleted]"
    assert res["top_comments"] == []


def test_inspect_full_url_form():
    body = (FIX / "reddit_thread.json").read_text()
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = reddit.inspect_reddit_thread("https://www.reddit.com/r/algotrading/comments/abc123/", client=client)
    assert res["ok"] is True


def test_inspect_empty_permalink():
    client = reddit._client(rate_per_min=1000)
    res = reddit.inspect_reddit_thread("  ", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_returns_parse_error_on_non_list_payload():
    client = reddit._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text='{"message": "Forbidden", "error": 403}')])
    res = reddit.inspect_reddit_thread("/r/algotrading/comments/x/", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "parse"
