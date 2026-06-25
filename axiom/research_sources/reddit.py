"""Reddit public JSON connector.

Uses the anonymous public endpoint (www.reddit.com/r/{sub}/search.json and thread .json).
OAuth can be layered on later via the registry's client_id/client_secret fields.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from axiom.research_sources._http import RateLimitExceeded, SourceHttpClient

_MAX_COMMENTS = 20
_MAX_DEPTH = 3
_BASE = "https://www.reddit.com"
_OAUTH_BASE = "https://oauth.reddit.com"
_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Reddit throttles / 403s generic bot UAs on its public JSON endpoints.
# Until we layer on OAuth, use a browser-like UA for the anonymous paths.
_REDDIT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _client(*, rate_per_min: int = 30) -> SourceHttpClient:
    return SourceHttpClient(
        per_domain={"www.reddit.com": rate_per_min, "oauth.reddit.com": rate_per_min},
        default_rate_per_min=rate_per_min,
    )


def _error_code_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    return f"http_{status // 100}xx"


def fetch_oauth_token(client_id: str, client_secret: str) -> dict[str, Any]:
    """Fetch an app-only Reddit OAuth token using configured script-app credentials."""
    cid = (client_id or "").strip()
    secret = (client_secret or "").strip()
    if not cid or not secret:
        return {
            "ok": False,
            "error_code": "auth_required",
            "error": "Reddit client_id/client_secret are required for OAuth.",
        }
    try:
        resp = httpx.post(
            _OAUTH_TOKEN_URL,
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": _REDDIT_UA},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error_code": "auth_failed", "error": str(exc)}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error_code": _error_code_for_status(resp.status_code),
            "error": f"oauth http {resp.status_code}",
        }
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "error_code": "parse", "error": "invalid oauth json"}
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return {"ok": False, "error_code": "auth_failed", "error": "missing reddit access token"}
    return {"ok": True, "access_token": token}


def search_reddit_posts(
    query: str,
    *,
    subs: list[str],
    limit: int = 10,
    client: SourceHttpClient | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty query"}
    if not subs:
        return {"ok": False, "error_code": "invalid_input", "error": "no subs configured"}
    client = client or _client()
    results: list[dict[str, Any]] = []
    for sub in subs:
        url = f"{_BASE}/r/{quote(sub)}/search.json"
        try:
            resp = client.get(
                url,
                headers={"User-Agent": _REDDIT_UA},
                params={"q": query, "restrict_sr": 1, "sort": "relevance", "t": "year", "limit": limit},
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
        for child in ((payload.get("data") or {}).get("children") or []):
            d = child.get("data") or {}
            results.append({
                "permalink": d.get("permalink"),
                "title": d.get("title"),
                "subreddit": d.get("subreddit"),
                "score": d.get("score"),
                "num_comments": d.get("num_comments"),
                "created_utc": d.get("created_utc"),
            })
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return {
        "ok": True,
        "source": "reddit",
        "query": query,
        "count": len(results),
        "results": results[:limit],
    }


def _walk_comments(node: dict[str, Any], flat: list[str], depth: int) -> None:
    if len(flat) >= _MAX_COMMENTS or depth > _MAX_DEPTH:
        return
    data = node.get("data") or {}
    body = data.get("body")
    score = data.get("score", 0)
    if body:
        flat.append(f"[{score}] {body}")
    replies = data.get("replies")
    if isinstance(replies, dict):
        for child in ((replies.get("data") or {}).get("children") or []):
            if len(flat) >= _MAX_COMMENTS:
                break
            _walk_comments(child, flat, depth + 1)


def inspect_reddit_thread(permalink: str, *, client: SourceHttpClient | None = None) -> dict[str, Any]:
    return inspect_reddit_thread_with_auth(permalink, client=client)


def inspect_reddit_thread_with_auth(
    permalink: str,
    *,
    client: SourceHttpClient | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    if not permalink.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty permalink"}
    client = client or _client()

    # Canonicalize: strip trailing slash, prepend base if starts with /, append .json
    ref = permalink.strip().rstrip("/")
    base = _OAUTH_BASE if access_token else _BASE
    if ref.startswith("/"):
        url = f"{base}{ref}.json"
    elif ref.startswith("http"):
        if access_token:
            path_start = ref.find("/r/")
            if path_start < 0:
                path_start = ref.find("/comments/")
            path = ref[path_start:] if path_start >= 0 else ref
            url = f"{base}{path}.json"
        else:
            url = f"{ref}.json"
    else:
        url = f"{base}/{ref}.json"

    try:
        headers = {"User-Agent": _REDDIT_UA}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        resp = client.get(url, headers=headers)
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
    if not isinstance(payload, list) or len(payload) < 2:
        return {"ok": False, "error_code": "parse", "error": "unexpected reddit response shape"}

    submission_listing = payload[0]
    comments_listing = payload[1]
    sub_children = ((submission_listing.get("data") or {}).get("children") or [])
    submission = (sub_children[0].get("data") if sub_children else {}) or {}

    title = submission.get("title", "") or ""
    selftext = submission.get("selftext", "") or ""

    flat: list[str] = []
    for child in ((comments_listing.get("data") or {}).get("children") or []):
        if len(flat) >= _MAX_COMMENTS:
            break
        _walk_comments(child, flat, depth=0)

    content = f"{title}\n\n{selftext}\n\n---\n" + "\n\n".join(flat)
    return {
        "ok": True,
        "source": "reddit",
        "title": title,
        "selftext": selftext,
        "top_comments": flat,
        "url": url,
        "content": content,
    }
