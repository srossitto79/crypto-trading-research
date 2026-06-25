"""GitHub API connector. Supports optional Personal Access Token for higher rate limits."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from axiom.research_sources._http import RateLimitExceeded, SourceHttpClient

_API = "https://api.github.com"
_ISSUE_BODY_MAX = 200


def _client(*, rate_per_min: int = 60, pat: str | None = None) -> SourceHttpClient:
    client = SourceHttpClient(
        per_domain={"api.github.com": rate_per_min},
        default_rate_per_min=rate_per_min,
    )
    client._pat = pat  # type: ignore[attr-defined]
    return client


def _auth_headers(client: SourceHttpClient) -> dict[str, str]:
    pat = getattr(client, "_pat", None)
    return {"Authorization": f"token {pat}"} if pat else {}


def _error_code_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    return f"http_{status // 100}xx"


def search_github_repos(
    query: str,
    *,
    orgs: list[str] | None = None,
    limit: int = 10,
    client: SourceHttpClient | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty query"}
    client = client or _client()
    q_parts = [query.strip(), "in:readme,description"]
    if orgs:
        for o in orgs:
            if o and str(o).strip():
                q_parts.append(f"org:{o.strip()}")
    q = " ".join(q_parts)
    try:
        resp = client.get(
            f"{_API}/search/repositories",
            params={"q": q, "per_page": limit},
            headers=_auth_headers(client),
        )
    except RateLimitExceeded as exc:
        return {"ok": False, "error_code": "rate_limited", "error": str(exc)}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error_code": _error_code_for_status(resp.status_code),
            "error": f"http {resp.status_code}",
        }
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "error_code": "parse", "error": "invalid json"}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {"ok": False, "error_code": "parse", "error": "unexpected github response shape"}
    results: list[dict[str, Any]] = []
    for it in items[:limit]:
        results.append({
            "full_name": it.get("full_name"),
            "description": it.get("description"),
            "stars": it.get("stargazers_count"),
            "language": it.get("language"),
            "pushed_at": it.get("pushed_at"),
            "html_url": it.get("html_url"),
        })
    return {
        "ok": True,
        "source": "github",
        "query": query,
        "count": len(results),
        "results": results,
    }


def inspect_github_repo(full_name: str, *, client: SourceHttpClient | None = None) -> dict[str, Any]:
    if not full_name.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty full_name"}
    if "/" not in full_name:
        return {"ok": False, "error_code": "invalid_input", "error": "full_name must be org/repo"}
    client = client or _client()
    enc = quote(full_name.strip(), safe="/")
    try:
        meta_resp = client.get(f"{_API}/repos/{enc}", headers=_auth_headers(client))
        readme_resp = client.get(
            f"{_API}/repos/{enc}/readme",
            headers={**_auth_headers(client), "Accept": "application/vnd.github.raw"},
        )
        issues_resp = client.get(
            f"{_API}/repos/{enc}/issues",
            params={"state": "all", "per_page": 5},
            headers=_auth_headers(client),
        )
    except RateLimitExceeded as exc:
        return {"ok": False, "error_code": "rate_limited", "error": str(exc)}
    # Metadata is load-bearing — hard-fail if it's missing
    if meta_resp.status_code >= 400:
        return {
            "ok": False,
            "error_code": _error_code_for_status(meta_resp.status_code),
            "error": f"http {meta_resp.status_code}",
        }
    # Issues endpoint failure is degraded but not fatal — log once via content note
    if issues_resp.status_code >= 400:
        issues_raw = []
    else:
        try:
            issues_raw = issues_resp.json()
        except ValueError:
            issues_raw = []
    # README 404 is common (repos with no README). Treat as empty content.
    if readme_resp.status_code == 404:
        readme_text = ""
    elif readme_resp.status_code >= 400:
        # Other 4xx/5xx on readme: still degraded, keep metadata+issues
        readme_text = ""
    else:
        readme_text = readme_resp.text

    try:
        meta = meta_resp.json()
    except ValueError:
        meta = {}
    if not isinstance(issues_raw, list):
        issues_raw = []
    issues = issues_raw[:5]

    issue_lines = []
    for i in issues:
        if not isinstance(i, dict):
            continue
        num = i.get("number")
        title = i.get("title") or ""
        body = (i.get("body") or "")[:_ISSUE_BODY_MAX]
        issue_lines.append(f"#{num} {title}: {body}")
    issues_summary = "\n".join(issue_lines)
    content = f"# {full_name}\n\n{readme_text}\n\n## Recent issues\n{issues_summary}"
    return {
        "ok": True,
        "source": "github",
        "full_name": full_name,
        "readme": readme_text,
        "recent_issues": issues,
        "metadata": meta,
        "content": content,
    }
