"""Podcast connector.

Parses podcast RSS feeds (feedparser) into episodes: title + show-notes text
(description / content:encoded / itunes summary) + the audio enclosure URL.

Audio->text transcription is a PLUGGABLE hook that is OFF by default
(:func:`transcribe_episode` returns a 'not configured' result). The show-notes
alone frequently carry the strategy description traders share, so this delivers
value immediately; a Whisper API or local whisper.cpp backend can be wired into
``transcribe_episode`` later without touching the harvest flow.
"""
from __future__ import annotations

import logging
from typing import Any

import feedparser

from axiom.research_sources._http import RateLimitExceeded, SourceHttpClient

log = logging.getLogger(__name__)

_SUMMARY_MAX = 2000
_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav", ".mp4")


def _client(*, rate_per_min: int = 20) -> SourceHttpClient:
    return SourceHttpClient(default_rate_per_min=rate_per_min)


def _error_code_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    return f"http_{status // 100}xx"


def _show_notes(entry: Any) -> str:
    """Richest available episode text: content:encoded > summary/description > subtitle."""
    content = getattr(entry, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        value = (first.get("value") if isinstance(first, dict) else getattr(first, "value", "")) or ""
        if value.strip():
            return value
    for attr in ("summary", "description", "subtitle"):
        value = getattr(entry, attr, "") or ""
        if value.strip():
            return value
    return ""


def _audio_url(entry: Any) -> str | None:
    enclosures = getattr(entry, "enclosures", None) or []

    def _href(enc: Any) -> str:
        return (enc.get("href") if isinstance(enc, dict) else getattr(enc, "href", "")) or ""

    def _type(enc: Any) -> str:
        return (enc.get("type") if isinstance(enc, dict) else getattr(enc, "type", "")) or ""

    for enc in enclosures:
        href = _href(enc)
        if href and (_type(enc).startswith("audio") or href.lower().endswith(_AUDIO_EXTENSIONS)):
            return href
    # Fallback: first enclosure with any href.
    for enc in enclosures:
        href = _href(enc)
        if href:
            return href
    return None


def _matches_query(text: str, query: str) -> bool:
    terms = [t.strip().lower() for t in query.split() if t.strip()]
    if not terms:
        return True
    blob = text.lower()
    return any(t in blob for t in terms)


def search_podcast_episodes(
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
            log.warning("podcast feed unavailable %s status=%s", feed_url, resp.status_code)
            continue
        parsed = feedparser.parse(resp.text)
        show = getattr(getattr(parsed, "feed", None), "title", "") or ""
        for entry in parsed.entries:
            title = getattr(entry, "title", "") or ""
            notes = _show_notes(entry)
            if not _matches_query(f"{title}\n{notes}", query):
                continue
            hits.append({
                "url": getattr(entry, "link", "") or "",
                "title": title,
                "show": show,
                "feed": feed_url,
                "published": getattr(entry, "published", None),
                "audio_url": _audio_url(entry),
                "summary": notes[:_SUMMARY_MAX],
            })
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return {
        "ok": True,
        "source": "podcast",
        "query": query,
        "count": len(hits),
        "results": hits[:limit],
    }


def inspect_podcast_episode(
    url: str,
    *,
    client: SourceHttpClient | None = None,
) -> dict[str, Any]:
    """Return an episode's show-notes (the connector's text payload).

    The url may be a podcast RSS feed (we take the first/matching episode) or an
    episode page. Audio transcription is intentionally NOT performed here — see
    :func:`transcribe_episode`.
    """
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

    title = ""
    show = ""
    notes = ""
    audio_url: str | None = None
    parsed = feedparser.parse(resp.text)
    if getattr(parsed, "entries", None):
        show = getattr(getattr(parsed, "feed", None), "title", "") or ""
        entry = parsed.entries[0]
        title = getattr(entry, "title", "") or ""
        notes = _show_notes(entry)
        audio_url = _audio_url(entry)
    else:
        # Episode page (not a feed) — extract article text as show-notes.
        try:
            import trafilatura

            notes = trafilatura.extract(resp.text, include_comments=False, favor_precision=True) or ""
        except Exception:
            notes = ""

    return {
        "ok": True,
        "source": "podcast",
        "url": url,
        "title": title,
        "show": show,
        "content": notes,
        "audio_url": audio_url,
        "transcript": None,
        "transcription_available": False,
        "note": (
            "Show-notes only. Audio transcription is a pluggable hook "
            "(transcribe_episode), OFF by default until a backend is configured."
        ),
    }


def transcribe_episode(audio_url: str, *, backend: str | None = None) -> dict[str, Any]:
    """Pluggable transcription seam — OFF by default.

    Wire a Whisper API or local whisper.cpp backend here later (gated behind a
    Research-Settings key). Until then this returns a clear 'not configured'
    result so callers degrade to show-notes instead of failing hard.
    """
    return {
        "ok": False,
        "error_code": "transcription_disabled",
        "error": "audio transcription backend not configured",
        "audio_url": audio_url,
        "backend": backend,
    }
