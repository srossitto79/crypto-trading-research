"""Podcast connector + detection + registry registration.

Show-notes-first harvesting; audio transcription is a pluggable hook that is OFF
by default. All unit-level (no network) via a fake HTTP client + sample RSS.
"""
from axiom.research_sources import podcast
from axiom.research_sources.url_ingest import detect_source_type

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Chat With Traders</title>
    <item>
      <title>Funding rate mean reversion on perps</title>
      <link>https://example.com/ep1</link>
      <description>The guest fades extreme funding on BTC perps with a 4h hold.</description>
      <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="123"/>
      <pubDate>Tue, 03 Jun 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Options wheel strategy</title>
      <link>https://example.com/ep2</link>
      <description>Selling cash-secured puts and covered calls on SPY.</description>
      <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg" length="456"/>
      <pubDate>Tue, 27 May 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


class _FakeResp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status


class _FakeClient:
    def __init__(self, text: str, status: int = 200) -> None:
        self._text = text
        self._status = status

    def get(self, _url: str) -> _FakeResp:
        return _FakeResp(self._text, self._status)


def test_search_podcast_episodes_filters_by_query():
    res = podcast.search_podcast_episodes(
        "funding", feeds=["https://feed"], client=_FakeClient(SAMPLE_RSS)
    )
    assert res["ok"] is True
    assert res["count"] == 1
    hit = res["results"][0]
    assert hit["title"] == "Funding rate mean reversion on perps"
    assert hit["audio_url"] == "https://cdn.example.com/ep1.mp3"
    assert hit["show"] == "Chat With Traders"
    assert "funding" in hit["summary"].lower()


def test_search_podcast_episodes_empty_query_rejected():
    res = podcast.search_podcast_episodes("", feeds=["https://feed"], client=_FakeClient(SAMPLE_RSS))
    assert res["ok"] is False
    assert res["error_code"] == "invalid_input"


def test_inspect_podcast_episode_returns_show_notes_not_transcript():
    res = podcast.inspect_podcast_episode("https://feed", client=_FakeClient(SAMPLE_RSS))
    assert res["ok"] is True
    assert res["source"] == "podcast"
    assert "fades extreme funding" in res["content"]
    assert res["audio_url"] == "https://cdn.example.com/ep1.mp3"
    # Transcription is a pluggable hook, OFF by default.
    assert res["transcript"] is None
    assert res["transcription_available"] is False


def test_transcribe_episode_is_disabled_by_default():
    res = podcast.transcribe_episode("https://cdn.example.com/ep1.mp3")
    assert res["ok"] is False
    assert res["error_code"] == "transcription_disabled"


def test_detect_source_type_routes_podcasts():
    assert detect_source_type("https://podcasts.apple.com/us/podcast/x/id123") == "podcast"
    assert detect_source_type("https://anchor.fm/some-show") == "podcast"
    assert detect_source_type("https://feeds.megaphone.fm/topdogtrading") == "podcast"
    # /podcast path on an arbitrary host also routes here.
    assert detect_source_type("https://chatwithtraders.com/feed/podcast/") == "podcast"
    # A plain article still falls back to blog.
    assert detect_source_type("https://example.com/some-article") == "blog"


def test_registry_resolves_podcast(monkeypatch):
    from axiom.research_sources import _registry

    monkeypatch.setattr(
        _registry,
        "_load_settings_block",
        lambda: {"podcast": {"enabled": True, "feeds": ["https://feeds.x/podcast"], "rate_limit_per_min": 20}},
    )
    resolved = _registry.resolve_registry("podcast")
    assert resolved is not None
    assert resolved["feeds"] == ["https://feeds.x/podcast"]
