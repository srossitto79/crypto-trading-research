from __future__ import annotations

from pathlib import Path

import httpx

from axiom.research_sources import github

FIX = Path(__file__).parent / "fixtures"


def _mock(client, responses):
    it = iter(responses)

    def handler(req):
        return next(it)

    client._transport = httpx.MockTransport(handler)


def test_search_parses_repos():
    body = (FIX / "github_search.json").read_text()
    client = github._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text=body)])
    res = github.search_github_repos("algorithmic trading", limit=10, client=client)
    assert res["ok"] is True
    assert res["count"] == 2
    first = res["results"][0]
    assert first["full_name"] == "quantopian/zipline"
    assert first["stars"] == 18000
    assert first["language"] == "Python"
    assert "github.com" in first["html_url"]


def test_search_empty_query():
    client = github._client(rate_per_min=1000)
    res = github.search_github_repos("", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_search_applies_orgs_filter():
    body = (FIX / "github_search.json").read_text()
    client = github._client(rate_per_min=1000)

    captured = {}

    def handler(req):
        captured["q"] = req.url.params.get("q")
        return httpx.Response(200, text=body)

    client._transport = httpx.MockTransport(handler)
    github.search_github_repos("trading", orgs=["quantopian", "hudson-and-thames"], client=client)
    assert "org:quantopian" in captured["q"]
    assert "org:hudson-and-thames" in captured["q"]


def test_search_injects_auth_header_when_pat_set():
    body = (FIX / "github_search.json").read_text()
    captured = {}

    def handler(req):
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text=body)

    client = github._client(rate_per_min=1000, pat="ghp_testtoken")
    client._transport = httpx.MockTransport(handler)
    github.search_github_repos("trading", client=client)
    assert captured["auth"] == "token ghp_testtoken"


def test_search_no_auth_header_without_pat():
    body = (FIX / "github_search.json").read_text()
    captured = {}

    def handler(req):
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text=body)

    client = github._client(rate_per_min=1000)
    client._transport = httpx.MockTransport(handler)
    github.search_github_repos("trading", client=client)
    assert captured["auth"] is None


def test_search_429_rate_limited():
    client = github._client(rate_per_min=1000)
    _mock(client, [httpx.Response(429), httpx.Response(429), httpx.Response(429)])
    res = github.search_github_repos("x", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "rate_limited"


def test_search_unexpected_payload_shape():
    client = github._client(rate_per_min=1000)
    _mock(client, [httpx.Response(200, text='{"message": "Forbidden"}')])
    res = github.search_github_repos("x", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "parse"


def test_inspect_bundles_readme_and_issues():
    meta = (FIX / "github_repo.json").read_text()
    readme = (FIX / "github_readme.md").read_text()
    issues = (FIX / "github_issues.json").read_text()
    client = github._client(rate_per_min=1000)
    _mock(client, [
        httpx.Response(200, text=meta),
        httpx.Response(200, text=readme),
        httpx.Response(200, text=issues),
    ])
    res = github.inspect_github_repo("quantopian/zipline", client=client)
    assert res["ok"] is True
    assert "# quantopian/zipline" in res["content"]
    assert "Zipline is a Pythonic" in res["content"]
    assert "#123 Strategy crashes on empty bar data" in res["content"]
    assert res["metadata"]["language"] == "Python"


def test_inspect_empty_full_name():
    client = github._client(rate_per_min=1000)
    res = github.inspect_github_repo("", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_malformed_full_name():
    client = github._client(rate_per_min=1000)
    res = github.inspect_github_repo("just-a-name", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_private_repo_404():
    meta_err = httpx.Response(404, text='{"message":"Not Found"}')
    client = github._client(rate_per_min=1000)
    _mock(client, [meta_err, meta_err, meta_err])
    res = github.inspect_github_repo("private/repo", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "http_4xx"


def test_inspect_handles_missing_readme_gracefully():
    meta = (FIX / "github_repo.json").read_text()
    issues = (FIX / "github_issues.json").read_text()
    client = github._client(rate_per_min=1000)
    _mock(client, [
        httpx.Response(200, text=meta),
        httpx.Response(404),  # README missing
        httpx.Response(200, text=issues),
    ])
    res = github.inspect_github_repo("quantopian/zipline", client=client)
    assert res["ok"] is True
    assert res["readme"] == ""
    assert len(res["recent_issues"]) >= 1
    assert res["metadata"]["language"] == "Python"


def test_inspect_hard_fails_on_metadata_404():
    client = github._client(rate_per_min=1000)
    # Metadata 404 (repo doesn't exist / is private). Readme/issues won't even be consulted before short-circuit.
    _mock(client, [
        httpx.Response(404),  # metadata
        httpx.Response(404),  # readme
        httpx.Response(404),  # issues
    ])
    res = github.inspect_github_repo("fictional/repo", client=client)
    assert res["ok"] is False
    assert res["error_code"] == "http_4xx"


def test_inspect_degrades_when_issues_endpoint_fails():
    meta = (FIX / "github_repo.json").read_text()
    readme = (FIX / "github_readme.md").read_text()
    client = github._client(rate_per_min=1000)
    # 500 triggers retries in SourceHttpClient (max_retries=3)
    _mock(client, [
        httpx.Response(200, text=meta),
        httpx.Response(200, text=readme),
        httpx.Response(500),  # issues endpoint broken (retry 1)
        httpx.Response(500),  # issues endpoint broken (retry 2)
        httpx.Response(500),  # issues endpoint broken (final)
    ])
    res = github.inspect_github_repo("quantopian/zipline", client=client)
    assert res["ok"] is True
    assert res["readme"]  # readme preserved
    assert res["recent_issues"] == []
