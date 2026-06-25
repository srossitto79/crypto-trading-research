from __future__ import annotations

import importlib
import json

import httpx


def _load_module():
    return importlib.import_module("axiom.research_sources.youtube")


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://www.youtube.com/"):
        self.text = text
        self.status_code = status_code
        self.url = url

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", self.url)
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("HTTP error", request=request, response=response)


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[tuple[str, str, dict | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self) -> None:
        return None

    def request(self, method: str, url: str, params=None, headers=None, follow_redirects=None):
        self.calls.append((method, url, params))
        if "search_query=missing+payload" in url or (params and params.get("search_query") == "missing payload"):
            return FakeResponse("<html><body>No payload here</body></html>")
        if "search_query=quant+trading" in url or (params and params.get("search_query") == "quant trading"):
            return FakeResponse(_SEARCH_HTML)
        if "watch?v=blocked_caps" in url:
            return FakeResponse(_WATCH_HTML_BLOCKED_CAPTIONS)
        if "watch?v=empty_transcript" in url:
            return FakeResponse(_WATCH_HTML_EMPTY_TRANSCRIPT)
        if "watch?v=bad_baseurl" in url:
            return FakeResponse(_WATCH_HTML_MISSING_BASEURL)
        if "watch?v=multi_track" in url:
            return FakeResponse(_WATCH_HTML_MULTIPLE_CAPTION_TRACKS)
        if "watch?v=abc123" in url:
            return FakeResponse(_WATCH_HTML)
        if "timedtext?v=blocked_caps" in url:
            return FakeResponse("", status_code=403, url=url)
        if "timedtext?v=empty_transcript" in url:
            return FakeResponse(_EMPTY_TRANSCRIPT_XML, url=url)
        if "timedtext?v=multi_track&lang=ar" in url:
            return FakeResponse(_EMPTY_TRANSCRIPT_XML, url=url)
        if "timedtext?v=multi_track&lang=en" in url:
            return FakeResponse(_TRANSCRIPT_XML, url=url)
        if "timedtext" in url:
            return FakeResponse(_TRANSCRIPT_XML, url=url)
        return FakeResponse("", status_code=404, url=url)

    def get(self, url: str, params=None, headers=None, follow_redirects=None):
        return self.request("GET", url, params=params, headers=headers, follow_redirects=follow_redirects)


_SEARCH_HTML = f"""
<html><body>
<script>var ytInitialData = {json.dumps({
    "contents": {
        "twoColumnSearchResultsRenderer": {
            "primaryContents": {
                "sectionListRenderer": {
                    "contents": [
                        {
                            "itemSectionRenderer": {
                                "contents": [
                                    {
                                        "videoRenderer": {
                                            "videoId": "abc123",
                                            "title": {"runs": [{"text": "Alpha trading walkthrough"}]},
                                            "ownerText": {
                                                "runs": [
                                                    {
                                                        "text": "Quant Channel",
                                                        "navigationEndpoint": {
                                                            "browseEndpoint": {
                                                                "canonicalBaseUrl": "/@quantchannel"
                                                            }
                                                        },
                                                    }
                                                ]
                                            },
                                            "descriptionSnippet": {"runs": [{"text": "A practical market example"}]},
                                            "lengthText": {"simpleText": "12:34"},
                                            "viewCountText": {"simpleText": "1,234 views"},
                                            "publishedTimeText": {"simpleText": "2 days ago"},
                                            "thumbnail": {
                                                "thumbnails": [
                                                    {"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"}
                                                ]
                                            },
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        }
    }
})};</script>
</body></html>
"""

_WATCH_HTML = f"""
<html><body>
<script>var ytInitialPlayerResponse = {json.dumps({
    "videoDetails": {
        "videoId": "abc123",
        "title": "Alpha trading walkthrough",
        "author": "Quant Channel",
        "shortDescription": "A practical market example",
    },
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "https://www.youtube.com/api/timedtext?v=abc123&lang=en",
                    "languageCode": "en",
                    "name": {"simpleText": "English"},
                }
            ]
        }
    }
})};</script>
</body></html>
"""

_WATCH_HTML_NO_CAPTIONS = """
<html><body>
<script>var ytInitialPlayerResponse = {"videoDetails": {"videoId": "no_caps"}};</script>
</body></html>
"""

_WATCH_HTML_BLOCKED_CAPTIONS = f"""
<html><body>
<script>var ytInitialPlayerResponse = {json.dumps({
    "videoDetails": {
        "videoId": "blocked_caps",
        "title": "Blocked captions example",
        "author": "Quant Channel",
    },
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "https://www.youtube.com/api/timedtext?v=blocked_caps&lang=en",
                    "languageCode": "en",
                }
            ]
        }
    }
})};</script>
</body></html>
"""

_WATCH_HTML_EMPTY_TRANSCRIPT = f"""
<html><body>
<script>var ytInitialPlayerResponse = {json.dumps({
    "videoDetails": {
        "videoId": "empty_transcript",
        "title": "Empty transcript example",
        "author": "Quant Channel",
    },
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "https://www.youtube.com/api/timedtext?v=empty_transcript&lang=en",
                    "languageCode": "en",
                }
            ]
        }
    }
})};</script>
</body></html>
"""

_WATCH_HTML_MISSING_BASEURL = f"""
<html><body>
<script>var ytInitialPlayerResponse = {json.dumps({
    "videoDetails": {
        "videoId": "bad_baseurl",
        "title": "Missing baseUrl example",
        "author": "Quant Channel",
    },
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "languageCode": "en",
                }
            ]
        }
    }
})};</script>
</body></html>
"""

_WATCH_HTML_UNEXPECTED_TRANSCRIPT_URL = f"""
<html><body>
<script>var ytInitialPlayerResponse = {json.dumps({
    "videoDetails": {
        "videoId": "unexpected_url",
        "title": "Unexpected transcript URL example",
        "author": "Quant Channel",
    },
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "https://example.com/transcript.xml",
                    "languageCode": "en",
                }
            ]
        }
    }
})};</script>
</body></html>
"""

_WATCH_HTML_MULTIPLE_CAPTION_TRACKS = f"""
<html><body>
<script>var ytInitialPlayerResponse = {json.dumps({
    "videoDetails": {
        "videoId": "multi_track",
        "title": "Multiple caption tracks example",
        "author": "Quant Channel",
        "shortDescription": "Select the source-language transcript track",
    },
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "https://www.youtube.com/api/timedtext?v=multi_track&lang=ar",
                    "languageCode": "ar",
                    "name": {"simpleText": "Arabic"},
                },
                {
                    "baseUrl": "https://www.youtube.com/api/timedtext?v=multi_track&lang=en&kind=asr",
                    "languageCode": "en",
                    "kind": "asr",
                    "vssId": "a.en",
                    "name": {"simpleText": "English (auto-generated)"},
                },
            ]
        }
    }
})};</script>
</body></html>
"""

_TRANSCRIPT_XML = """<?xml version="1.0" encoding="utf-8"?>
<transcript>
  <text start="0.0" dur="2.5">Hello world</text>
  <text start="2.5" dur="1.0">Second line</text>
</transcript>
"""

_EMPTY_TRANSCRIPT_XML = """<?xml version="1.0" encoding="utf-8"?>
<transcript>
  <text start="0.0" dur="2.5">   </text>
  <text start="2.5" dur="1.0"></text>
</transcript>
"""


def test_search_youtube_videos_returns_normalized_candidates_from_yt_initial_data(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)

    payload = youtube.search_youtube_videos("quant trading", max_results=1)

    assert payload["query"] == "quant trading"
    assert payload["results"] == [
        {
            "video_id": "abc123",
            "title": "Alpha trading walkthrough",
            "url": "https://www.youtube.com/watch?v=abc123",
            "channel_name": "Quant Channel",
            "channel_url": "https://www.youtube.com/@quantchannel",
            "description": "A practical market example",
            "duration": "12:34",
            "views": "1,234 views",
            "published": "2 days ago",
            "thumbnail_url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
        }
    ]


def test_search_youtube_videos_tolerates_missing_payload_and_returns_empty_results(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)

    payload = youtube.search_youtube_videos("missing payload", max_results=5)

    assert payload["results"] == []


def test_inspect_youtube_video_returns_transcript_when_captions_exist(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)

    payload = youtube.inspect_youtube_video("https://youtu.be/abc123")

    assert payload["status"] == "ok"
    assert payload["url"] == "https://www.youtube.com/watch?v=abc123"
    assert payload["transcript"] == [
        {"start": 0.0, "dur": 2.5, "text": "Hello world"},
        {"start": 2.5, "dur": 1.0, "text": "Second line"},
    ]


def test_inspect_youtube_video_returns_unavailable_when_no_captions_exist(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)
    monkeypatch.setattr(youtube, "_fetch_watch_page", lambda client, url: _WATCH_HTML_NO_CAPTIONS)

    payload = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=no_caps")

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "captions_unavailable"


def test_inspect_youtube_video_rejects_invalid_youtube_url(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)

    payload = youtube.inspect_youtube_video("https://notyoutube.com/watch?v=abc123")

    assert payload["status"] == "error"
    assert payload["reason"] == "invalid_youtube_url"


def test_inspect_youtube_video_returns_blocked_when_caption_fetch_is_blocked(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)

    payload = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=blocked_caps")

    assert payload["status"] == "blocked"
    assert payload["reason"] == "transcript_fetch_blocked"
    assert payload["http_status"] == 403


def test_inspect_youtube_video_returns_unavailable_when_caption_track_missing_base_url(monkeypatch):
    youtube = _load_module()
    client = FakeClient()
    monkeypatch.setattr(youtube.httpx, "Client", lambda *args, **kwargs: client)

    payload = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=bad_baseurl")

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "caption_track_missing_base_url"
    assert not any("timedtext" in call[1] for call in client.calls)


def test_inspect_youtube_video_blocks_unexpected_transcript_url(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)
    monkeypatch.setattr(youtube, "_fetch_watch_page", lambda client, url: _WATCH_HTML_UNEXPECTED_TRANSCRIPT_URL)

    payload = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=unexpected_url")

    assert payload["status"] == "blocked"
    assert payload["reason"] == "transcript_url_not_allowed"


def test_inspect_youtube_video_marks_empty_transcript_unavailable(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)

    payload = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=empty_transcript")

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "transcript_empty"


def test_inspect_youtube_video_prefers_english_track_over_first_caption_track(monkeypatch):
    youtube = _load_module()
    monkeypatch.setattr(youtube.httpx, "Client", FakeClient)
    monkeypatch.setattr(youtube, "_fetch_watch_page", lambda client, url: _WATCH_HTML_MULTIPLE_CAPTION_TRACKS)

    payload = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=multi_track")

    assert payload["status"] == "ok"
    assert payload["transcript"] == [
        {"start": 0.0, "dur": 2.5, "text": "Hello world"},
        {"start": 2.5, "dur": 1.0, "text": "Second line"},
    ]
