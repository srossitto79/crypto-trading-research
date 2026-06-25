"""Blog/RSS connector. Parses RSS 2.0 and Atom via feedparser; extracts article plaintext via trafilatura."""
from __future__ import annotations

import logging
from typing import Any

import feedparser
import trafilatura

from axiom.research_sources._http import RateLimitExceeded, SourceHttpClient

log = logging.getLogger(__name__)

_SUMMARY_MAX = 500


def _client(*, rate_per_min: int = 30) -> SourceHttpClient:
    return SourceHttpClient(default_rate_per_min=rate_per_min)


def _error_code_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    return f"http_{status // 100}xx"


def _matches_query(text: str, query: str) -> bool:
    terms = [t.strip().lower() for t in query.split() if t.strip()]
    if not terms:
        return True
    blob = text.lower()
    return any(t in blob for t in terms)


def search_blog_articles(
    query: str,
    *,
    feeds: list[str],
    limit: int = 10,
    client: SourceHttpClient | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty query"}
    if not feeds:
        return {"ok": False, "error_code": "invalid_input", "error": "no feeds configured"}
    client = client or _client()
    hits: list[dict[str, Any]] = []
    for feed_url in feeds:
        try:
            resp = client.get(feed_url)
        except RateLimitExceeded as exc:
            return {"ok": False, "error_code": "rate_limited", "error": str(exc)}
        if resp.status_code >= 400:
            log.warning("blog feed unavailable %s status=%s", feed_url, resp.status_code)
            continue
        parsed = feedparser.parse(resp.text)
        for entry in parsed.entries:
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            if not _matches_query(f"{title}\n{summary}", query):
                continue
            hits.append({
                "url": getattr(entry, "link", "") or "",
                "title": title,
                "feed": feed_url,
                "published": getattr(entry, "published", None),
                "summary": summary[:_SUMMARY_MAX],
            })
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return {
        "ok": True,
        "source": "blog",
        "query": query,
        "count": len(hits),
        "results": hits[:limit],
    }


def inspect_blog_article(url: str, *, client: SourceHttpClient | None = None) -> dict[str, Any]:
    if not url.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty url"}
    client = client or _client()
    try:
        resp = client.get(url)
    except RateLimitExceeded as exc:
        return {"ok": False, "error_code": "rate_limited", "error": str(exc)}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error_code": _error_code_for_status(resp.status_code),
            "error": f"http {resp.status_code}",
        }
    html = resp.text
    extracted = trafilatura.extract(html, include_comments=False, favor_precision=True) or ""
    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = getattr(meta, "title", "") or ""
    except Exception:
        title = ""
    return {
        "ok": True,
        "source": "blog",
        "url": url,
        "title": title,
        "content": extracted,
    }
