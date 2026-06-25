from __future__ import annotations

from pathlib import Path

import httpx

from axiom.research_sources import forum

FIX = Path(__file__).parent / "fixtures"


def _mock(client, responses):
    it = iter(responses)

    def handler(req):
        return next(it)

    client._transport = httpx.MockTransport(handler)


def test_search_elitetrader_parses_threads():
    body = (FIX / "forum_elitetrader_search.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = forum.search_forum_threads("mean reversion", sites=["elitetrader.com"], limit=10, client=client)
    assert res["ok"] is True
    assert res["count"] >= 2
    assert any("Mean Reversion" in r["title"] for r in res["results"])
    assert all(r["site"] == "elitetrader.com" for r in res["results"])
    assert all(r["url"].startswith("https://elitetrader.com/") for r in res["results"])


def test_search_quantconnect_parses():
    body = (FIX / "forum_quantconnect_search.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = forum.search_forum_threads("cointegration", sites=["quantconnect.com"], limit=10, client=client)
    assert res["ok"] is True
    assert res["count"] >= 1


def test_search_quantnet_parses():
    body = (FIX / "forum_quantnet_search.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = forum.search_forum_threads("interview", sites=["quantnet.com"], limit=10, client=client)
    assert res["ok"] is True
    assert res["count"] >= 1


def test_search_aggregates_multiple_sites():
    et = (FIX / "forum_elitetrader_search.html").read_text()
    qc = (FIX / "forum_quantconnect_search.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=et), httpx.Response(200, text=qc)])
    res = forum.search_forum_threads("x", sites=["elitetrader.com", "quantconnect.com"], limit=10, client=client)
    assert res["ok"] is True
    sites_seen = {r["site"] for r in res["results"]}
    assert sites_seen == {"elitetrader.com", "quantconnect.com"}


def test_search_empty_query():
    client = forum._client(rate_per_min=1000)
    res = forum.search_forum_threads("", sites=["elitetrader.com"], client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_search_no_sites():
    client = forum._client(rate_per_min=1000)
    res = forum.search_forum_threads("x", sites=[], client=client)
    assert res["ok"] is False


def test_search_unknown_site_skipped():
    client = forum._client(rate_per_min=1000)
    # Unknown site — no HTTP call will be made; ensure graceful zero-result
    res = forum.search_forum_threads("x", sites=["unknown-forum.com"], limit=5, client=client)
    assert res["ok"] is True
    assert res["count"] == 0


def test_search_404_skipped():
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(404), httpx.Response(404), httpx.Response(404)])
    res = forum.search_forum_threads("x", sites=["elitetrader.com"], limit=5, client=client)
    assert res["ok"] is True
    assert res["count"] == 0


def test_inspect_elitetrader_extracts_posts():
    body = (FIX / "forum_elitetrader_thread.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = forum.inspect_forum_thread("https://www.elitetrader.com/et/threads/mean-reversion-debate.12345/", client=client)
    assert res["ok"] is True
    assert "Mean Reversion Debate" in res["title"]
    assert len(res["posts"]) == 3
    assert "15 years" in res["content"]
    assert "ADX-filtered" in res["content"]


def test_inspect_quantconnect_extracts_posts():
    body = (FIX / "forum_quantconnect_thread.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = forum.inspect_forum_thread("https://www.quantconnect.com/forum/discussion/12345/cointegration-test-implementation", client=client)
    assert res["ok"] is True
    assert "Cointegration" in res["title"]
    assert len(res["posts"]) == 2


def test_inspect_quantnet_extracts_posts():
    body = (FIX / "forum_quantnet_thread.html").read_text()
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = forum.inspect_forum_thread("https://quantnet.com/threads/statistical-arbitrage-interview.12345/", client=client)
    assert res["ok"] is True
    assert len(res["posts"]) >= 1


def test_inspect_empty_url():
    client = forum._client(rate_per_min=1000)
    res = forum.inspect_forum_thread("", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_unknown_site_returns_invalid_input():
    client = forum._client(rate_per_min=1000)
    res = forum.inspect_forum_thread("https://unknown-forum.com/threads/x/", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_404():
    client = forum._client(rate_per_min=1000)
    _mock(client, [httpx.Response(404), httpx.Response(404), httpx.Response(404)])
    res = forum.inspect_forum_thread("https://www.elitetrader.com/et/threads/missing.x/", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "http_4xx"
