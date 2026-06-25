"""2026-06-13 — Discord REST sink must circuit-break on a persistent 403 ("Missing
Access") instead of re-POSTing + warning on every notification. Overnight: ~196
403s, each paired with a Axiom.bot WARNING and a Axiom.notifications WARNING."""
import logging

import httpx
import pytest

from axiom import bot


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_breaker(monkeypatch):
    bot._DISCORD_FORBIDDEN_CHANNELS.clear()
    monkeypatch.setattr(bot, "get_bot_token", lambda: "test-token")
    yield
    bot._DISCORD_FORBIDDEN_CHANNELS.clear()


def test_send_sync_403_trips_circuit_and_returns_false(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _DummyResponse(403, {"message": "Missing Access"}))
    assert bot.send_sync("heartbeat", "hi", channel_id="999") is False
    assert "999" in bot._DISCORD_FORBIDDEN_CHANNELS


def test_send_sync_skips_http_when_circuit_open(monkeypatch):
    bot._trip_discord_channel_circuit("999")

    def _boom(*a, **k):
        raise AssertionError("httpx.post should not be called when the circuit is open")

    monkeypatch.setattr(httpx, "post", _boom)
    assert bot.send_sync("heartbeat", "hi", channel_id="999") is False


def test_send_sync_single_warning_per_cooldown(monkeypatch, caplog):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _DummyResponse(403, {"message": "Missing Access"}))
    with caplog.at_level(logging.WARNING, logger="axiom.bot"):
        for _ in range(3):
            bot.send_sync("heartbeat", "hi", channel_id="999")
    lacks = [r for r in caplog.records if "lacks access to channel" in r.message]
    failed = [r for r in caplog.records if "Discord REST send failed" in r.message]
    assert len(lacks) == 1
    assert len(failed) == 0


def test_send_sync_recovers_after_cooldown_expiry(monkeypatch):
    # Real recovery path: an OPEN circuit short-circuits before the request, so a
    # re-granted channel can only recover once the cooldown expires — then the next
    # send actually reaches the API, succeeds, and leaves no breaker key.
    bot._trip_discord_channel_circuit("999")
    expiry = bot._DISCORD_FORBIDDEN_CHANNELS["999"]
    monkeypatch.setattr(bot.time, "time", lambda: expiry + 1)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _DummyResponse(200, {}))
    assert bot.send_sync("heartbeat", "hi", channel_id="999") is True
    assert "999" not in bot._DISCORD_FORBIDDEN_CHANNELS


def test_send_sync_non_403_still_raises(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _DummyResponse(500, {"message": "boom"}))
    with pytest.raises(RuntimeError):
        bot.send_sync("heartbeat", "hi", channel_id="999")
    assert "999" not in bot._DISCORD_FORBIDDEN_CHANNELS  # transient errors stay retryable


def test_circuit_expiry_evicts(monkeypatch):
    bot._trip_discord_channel_circuit("999")
    # Jump past the cooldown window.
    monkeypatch.setattr(bot.time, "time", lambda: bot._DISCORD_FORBIDDEN_CHANNELS["999"] + 1)
    assert bot._discord_channel_circuit_open("999") is False
    assert "999" not in bot._DISCORD_FORBIDDEN_CHANNELS


def test_send_thread_sync_403_trips_circuit(monkeypatch):
    # 403 on the thread-create POST.
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _DummyResponse(403, {"message": "Missing Access"}))
    assert bot.send_thread_sync("heartbeat", "t", "m", channel_id="999") is False
    assert "999" in bot._DISCORD_FORBIDDEN_CHANNELS
