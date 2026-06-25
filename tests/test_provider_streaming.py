"""SSE parsing tests for the provider streaming helpers (OpenAI + Anthropic)."""
from __future__ import annotations

import asyncio

import axiom.agents.providers as providers


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(self._lines)


def _patch(monkeypatch, lines):
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda *a, **k: _FakeClient(lines))


def _drain(agen):
    async def _run():
        out = []
        async for ev in agen:
            out.append(ev)
        return out

    return asyncio.run(_run())


def test_stream_openai_chat_parses_text_and_tool_call(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"get_portfolio_status","arguments":"{}"}}]}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}',
        'data: [DONE]',
    ]
    _patch(monkeypatch, lines)
    events = _drain(providers._stream_openai_chat("http://x", {}, {"model": "m", "messages": []}))

    deltas = [e["text"] for e in events if e["type"] == "text"]
    assert deltas == ["Hel", "lo"]
    done = [e for e in events if e["type"] == "done"][0]
    resp = done["response"]
    assert resp.text == "Hello"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "get_portfolio_status"
    assert resp.usage.get("completion_tokens") == 2


def test_stream_anthropic_messages_parses_text_and_tool_use(monkeypatch):
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":3}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" there"}}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"get_strategy_detail"}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"strategy_id\\":"}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"S1\\"}"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
        'data: {"type":"message_stop"}',
    ]
    _patch(monkeypatch, lines)
    events = _drain(providers._stream_anthropic_messages("http://x", {}, {"model": "m", "messages": []}))

    deltas = [e["text"] for e in events if e["type"] == "text"]
    assert deltas == ["Hi", " there"]
    resp = [e for e in events if e["type"] == "done"][0]["response"]
    assert resp.text == "Hi there"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "get_strategy_detail"
    assert resp.tool_calls[0].input == {"strategy_id": "S1"}


def test_default_stream_falls_back_to_call(monkeypatch):
    """A provider with no native streaming still yields text + done via call()."""

    class _Dummy(providers.ToolCallProvider):
        async def call(self, model_id, messages, system, tools, token):
            return providers.ProviderResponse(text="full answer", tool_calls=[], usage={})

    events = _drain(_Dummy().stream("m", [], "sys", [], "tok"))
    assert [e["type"] for e in events] == ["text", "done"]
    assert events[0]["text"] == "full answer"
