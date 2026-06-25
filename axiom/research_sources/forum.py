"""Generic forum connector with per-site HTML adapters.

Each adapter defines search URL builder + CSS selectors. Adapter-per-site keeps
blast radius small when a site redesigns. Selectors may need periodic tuning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from axiom.research_sources._http import RateLimitExceeded, SourceHttpClient


@dataclass(frozen=True, slots=True)
class ForumAdapter:
    site: str
    search_url: Callable[[str], str]
    result_selector: str
    result_link_selector: str
    thread_selector: str
    post_selector: str


ADAPTERS: dict[str, ForumAdapter] = {
    "elitetrader.com": ForumAdapter(
        site="elitetrader.com",
        search_url=lambda q: f"https://www.elitetrader.com/et/search/0/?q={quote(q)}&o=relevance&t=post",
        result_selector="li.block-row",
        result_link_selector="a.thread-link",
        thread_selector="article.message",
        post_selector=".message-body",
    ),
    "quantconnect.com": ForumAdapter(
        site="quantconnect.com",
        search_url=lambda q: f"https://www.quantconnect.com/forum/search?value={quote(q)}",
        result_selector="div.discussion-item",
        result_link_selector="a.discussion-link",
        thread_selector="div.post",
        post_selector=".post-body",
    ),
    "quantnet.com": ForumAdapter(
        site="quantnet.com",
        search_url=lambda q: f"https://quantnet.com/search/?q={quote(q)}",
        result_selector="li.block-row",
        result_link_selector="a.thread-link",
        thread_selector="article.message",
        post_selector=".message-body",
    ),
}


def _client(*, rate_per_min: int = 20) -> SourceHttpClient:
    return SourceHttpClient(default_rate_per_min=rate_per_min)


def _error_code_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    return f"http_{status // 100}xx"


def _adapter_for(site_or_url: str) -> ForumAdapter | None:
    key = site_or_url.lower().strip()
    if key in ADAPTERS:
        return ADAPTERS[key]
    host = (urlparse(key).hostname or "").lstrip("www.")
    for site, adapter in ADAPTERS.items():
        if site in host:
            return adapter
    return None


def search_forum_threads(
    query: str,
    *,
    sites: list[str],
    limit: int = 10,
    client: SourceHttpClient | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty query"}
    if not sites:
        return {"ok": False, "error_code": "invalid_input", "error": "no sites configured"}
    client = client or _client()
    results: list[dict[str, Any]] = []
    for site in sites:
        adapter = _adapter_for(site)
        if adapter is None:
            continue
        try:
            resp = client.get(adapter.search_url(query))
        except RateLimitExceeded as exc:
            return {"ok": False, "error_code": "rate_limited", "error": str(exc)}
        if resp.status_code >= 400:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for el in soup.select(adapter.result_selector):
            if len(results) >= limit:
                break
            link = el.select_one(adapter.result_link_selector) or el.find("a", href=True)
            if link is None or not link.get("href"):
                continue
            href = link["href"]
            url = urljoin(f"https://{adapter.site}/", href)
            results.append({
                "url": url,
                "title": link.get_text(strip=True) or "",
                "site": adapter.site,
                "replies": None,
                "last_post_at": None,
            })
        if len(results) >= limit:
            break
    return {
        "ok": True,
        "source": "forum",
        "query": query,
        "count": len(results),
        "results": results,
    }


def inspect_forum_thread(url: str, *, client: SourceHttpClient | None = None) -> dict[str, Any]:
    if not url.strip():
        return {"ok": False, "error_code": "invalid_input", "error": "empty url"}
    client = client or _client()
    adapter = _adapter_for(url)
    if adapter is None:
        return {"ok": False, "error_code": "invalid_input", "error": f"no adapter for {url}"}
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
    soup = BeautifulSoup(resp.text, "html.parser")
    title_el = soup.find("h1") or soup.find("h2")
    title = title_el.get_text(strip=True) if title_el else ""
    posts: list[dict[str, Any]] = []
    for thread in soup.select(adapter.thread_selector)[:30]:
        body = thread.select_one(adapter.post_selector)
        if body is None:
            continue
        posts.append({
            "author": "",
            "body": body.get_text("\n", strip=True),
            "date": "",
        })
    content = f"{title}\n\n" + "\n\n---\n\n".join(p["body"] for p in posts)
    return {
        "ok": True,
        "source": "forum",
        "site": adapter.site,
        "url": url,
        "title": title,
        "posts": posts,
        "content": content,
    }
