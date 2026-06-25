"""Phase 4 / P4-T02 — AnthropicProvider unit tests.

Confirms the direct Anthropic Messages API round-trip:
- request shape matches the API contract (x-api-key, anthropic-version,
  model/messages/system/tools/max_tokens),
- response with a `tool_use` block produces a populated
  ``ProviderResponse.tool_calls`` list,
- ``base_url`` profile override redirects the request,
- factory wiring resolves ``anthropic`` to ``AnthropicProvider``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from axiom.agents.providers import (
    AnthropicProvider,
    ProviderResponse,
    ToolCall,
    get_provider,
)


def _mock_response(content_blocks: list[dict], stop_reason: str = "tool_use") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 12, "output_tokens": 34},
    })
    return resp


def test_factory_resolves_anthropic() -> None:
    provider = get_provider("anthropic")
    assert isinstance(provider, AnthropicProvider)


def test_anthropic_default_base_url() -> None:
    with patch("axiom.agents.providers.get_profile", return_value=None):
        assert AnthropicProvider._get_base_url() == "https://api.anthropic.com"


def test_anthropic_base_url_override() -> None:
    with patch(
        "axiom.agents.providers.get_profile",
        return_value={"base_url": "https://proxy.example.com/"},
    ):
        assert AnthropicProvider._get_base_url() == "https://proxy.example.com"


def test_anthropic_call_returns_tool_calls() -> None:
    provider = AnthropicProvider()
    blocks = [
        {"type": "text", "text": "Calling a tool now."},
        {
            "type": "tool_use",
            "id": "toolu_abc",
            "name": "list_strategies",
            "input": {"limit": 10},
        },
    ]

    fake_resp = _mock_response(blocks)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("axiom.agents.providers.httpx.AsyncClient", return_value=mock_client), \
         patch("axiom.agents.providers.get_profile", return_value=None):
        result = asyncio.run(provider.call(
            model_id="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "list strategies"}],
            system="you are helpful",
            tools=[{"name": "list_strategies", "description": "x", "input_schema": {}}],
            token="sk-ant-test",
        ))

    assert isinstance(result, ProviderResponse)
    assert "Calling a tool now." in result.text
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert isinstance(call, ToolCall)
    assert call.id == "toolu_abc"
    assert call.name == "list_strategies"
    assert call.input == {"limit": 10}
    assert result.stop is False  # tool calls present, not end_turn

    # Verify request shape
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://api.anthropic.com/v1/messages"
    headers = call_args[1]["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    body = call_args[1]["json"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["system"] == "you are helpful"
    assert body["tools"][0]["name"] == "list_strategies"


def test_anthropic_text_only_response_stops() -> None:
    provider = AnthropicProvider()
    blocks = [{"type": "text", "text": "Done."}]
    fake_resp = _mock_response(blocks, stop_reason="end_turn")
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("axiom.agents.providers.httpx.AsyncClient", return_value=mock_client), \
         patch("axiom.agents.providers.get_profile", return_value=None):
        result = asyncio.run(provider.call(
            model_id="claude-sonnet-4-6",
            messages=[],
            system="",
            tools=[],
            token="sk",
        ))

    assert result.text == "Done."
    assert result.tool_calls == []
    assert result.stop is True


def test_anthropic_append_tool_results_uses_tool_result_blocks() -> None:
    provider = AnthropicProvider()
    messages: list[dict] = []
    provider.append_tool_results(messages, [("toolu_abc", "OK"), ("toolu_def", "DATA")])

    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert msg["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_abc",
        "content": "OK",
    }
    assert msg["content"][1]["tool_use_id"] == "toolu_def"
