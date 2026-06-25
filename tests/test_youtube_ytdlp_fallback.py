"""Tests for the yt-dlp fallback layer in Axiom.research_sources.youtube.

The primary scraper (already covered in test_youtube_research_sources.py) does
the happy-path metadata + caption lookup. These tests verify behaviour when the
scraper returns a non-ok status — we expect yt-dlp subtitle extraction to run,
and the final envelope to carry a valid transcript + transcript_source flag.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from axiom.research_sources import youtube


SCRAPER_OK_TRANSCRIPT = [
    {"start": 0.0, "dur": 2.5, "text": "Hello traders"},
    {"start": 2.5, "dur": 3.0, "text": "Today we talk about mean reversion"},
]
SCRAPER_METADATA = {
    "title": "A Good Video",
    "channel_name": "Trader Joe",
    "description_excerpt": "Some description",
    "url": "https://www.youtube.com/watch?v=VID12345",
    "video_id": "VID12345",
}


def _scraper_ok():
    return {"status": "ok", **SCRAPER_METADATA, "transcript": list(SCRAPER_OK_TRANSCRIPT)}


def _scraper_captions_unavailable():
    return {"status": "unavailable", "reason": "captions_unavailable", **SCRAPER_METADATA}


def _scraper_hard_error():
    return {"status": "error", "reason": "invalid_youtube_url", "url": "bad"}


YTDLP_TRANSCRIPT = [
    {"start": 0.0, "dur": 2.0, "text": "Fallback transcript line one"},
    {"start": 2.0, "dur": 2.5, "text": "Fallback transcript line two"},
]


# ---- inspect_youtube_video dispatcher ----


def test_scraper_succeeds_yt_dlp_not_called():
    with patch.object(youtube, "_inspect_via_scraper", return_value=_scraper_ok()) as scraper, \
         patch.object(youtube, "_fetch_transcript_via_ytdlp") as ytdlp:
        result = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=VID12345")
    assert result["status"] == "ok"
    assert result["transcript"] == SCRAPER_OK_TRANSCRIPT
    assert result["transcript_source"] == "scraper"
    scraper.assert_called_once()
    ytdlp.assert_not_called()


def test_scraper_unavailable_falls_back_to_ytdlp():
    with patch.object(youtube, "_inspect_via_scraper", return_value=_scraper_captions_unavailable()), \
         patch.object(youtube, "_fetch_transcript_via_ytdlp", return_value=list(YTDLP_TRANSCRIPT)):
        result = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=VID12345")
    assert result["status"] == "ok"
    assert result["transcript"] == YTDLP_TRANSCRIPT
    assert result["transcript_source"] == "yt_dlp"
    # Scraper metadata preserved when available
    assert result["title"] == "A Good Video"
    assert result["channel_name"] == "Trader Joe"


def test_ytdlp_also_empty_returns_original_scraper_failure():
    with patch.object(youtube, "_inspect_via_scraper", return_value=_scraper_captions_unavailable()), \
         patch.object(youtube, "_fetch_transcript_via_ytdlp", return_value=None):
        result = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=VID12345")
    # We return the scraper's error so operators get the original diagnostic
    assert result["status"] == "unavailable"
    assert result["reason"] == "captions_unavailable"
    assert "transcript" not in result or not result.get("transcript")


def test_invalid_url_no_video_id_no_fallback_attempted():
    with patch.object(youtube, "_inspect_via_scraper", return_value=_scraper_hard_error()), \
         patch.object(youtube, "_fetch_transcript_via_ytdlp") as ytdlp:
        result = youtube.inspect_youtube_video("bad")
    # Hard error (no video_id extractable) → no yt-dlp call
    assert result["status"] == "error"
    ytdlp.assert_not_called()


def test_scraper_has_no_metadata_fallback_fills_from_ytdlp():
    """When the scraper fails before pulling title/channel, yt-dlp metadata fills the gap."""
    scraper_no_meta = {"status": "blocked", "reason": "transcript_fetch_blocked"}
    ytdlp_meta = {"title": "yt-dlp-title", "channel_name": "yt-dlp-channel", "description_excerpt": "yt-dlp-desc"}
    with patch.object(youtube, "_inspect_via_scraper", return_value=scraper_no_meta), \
         patch.object(youtube, "_fetch_transcript_via_ytdlp", return_value=list(YTDLP_TRANSCRIPT)), \
         patch.object(youtube, "_fetch_ytdlp_metadata", return_value=ytdlp_meta):
        result = youtube.inspect_youtube_video("https://www.youtube.com/watch?v=VID99999")
    assert result["status"] == "ok"
    assert result["title"] == "yt-dlp-title"
    assert result["channel_name"] == "yt-dlp-channel"
    assert result["transcript"] == YTDLP_TRANSCRIPT


# ---- _parse_json3_transcript: pure function, no network ----


def test_parse_json3_happy_path():
    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2500, "segs": [{"utf8": "Hello"}, {"utf8": " world"}]},
            {"tStartMs": 2500, "dDurationMs": 3000, "segs": [{"utf8": "Second line"}]},
        ]
    }
    result = youtube._parse_json3_transcript(payload)
    assert result == [
        {"start": 0.0, "dur": 2.5, "text": "Hello world"},
        {"start": 2.5, "dur": 3.0, "text": "Second line"},
    ]


def test_parse_json3_skips_empty_events():
    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": ""}]},  # blank
            {"tStartMs": 1000, "dDurationMs": 2000},  # no segs
            {"tStartMs": 3000, "dDurationMs": 1500, "segs": [{"utf8": "real line"}]},
        ]
    }
    result = youtube._parse_json3_transcript(payload)
    assert result == [{"start": 3.0, "dur": 1.5, "text": "real line"}]


def test_parse_json3_malformed_returns_empty():
    assert youtube._parse_json3_transcript(None) == []
    assert youtube._parse_json3_transcript({}) == []
    assert youtube._parse_json3_transcript({"events": "not-a-list"}) == []
    assert youtube._parse_json3_transcript({"events": [None, 42, "str"]}) == []


def test_parse_json3_non_int_timestamps_coerced_or_zeroed():
    payload = {"events": [{"tStartMs": "bad", "dDurationMs": None, "segs": [{"utf8": "x"}]}]}
    result = youtube._parse_json3_transcript(payload)
    assert result == [{"start": 0.0, "dur": 0.0, "text": "x"}]


# ---- _safe_video_id: extracts from various URL shapes without raising ----


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc123", "abc123"),
        ("https://youtu.be/xyz789", "xyz789"),
        ("https://m.youtube.com/watch?v=mob111", "mob111"),
        ("not a url", ""),
        ("", ""),
    ],
)
def test_safe_video_id(url, expected):
    assert youtube._safe_video_id(url) == expected
