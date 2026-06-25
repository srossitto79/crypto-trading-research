"""Schema normalization tests for Brain response payloads."""

import asyncio

import axiom.ai as ai_mod
import axiom.brain as brain_mod
from axiom.brain import normalize_brain_decision, parse_brain_decision


def test_parse_brain_decision_accepts_markdown_json_block():
    raw = """
Here you go:
```json
{
  "summary": "Cycle complete.",
  "observations": ["No kill-switch trigger."],
  "actions": [
    {
      "action": "transition_stage",
      "strategy_id": "S1234",
      "to_stage": "backtesting",
      "reason": "Ready for validation"
    }
  ]
}
```
"""
    decision = parse_brain_decision(raw)
    assert decision.summary == "Cycle complete."
    assert len(decision.actions) == 1


def test_normalize_brain_decision_falls_back_for_unstructured_text():
    raw = "I reviewed the system. No action required."
    decision = normalize_brain_decision(raw)
    assert decision.summary.startswith("I reviewed the system")
    assert decision.actions == []
    assert decision.observations


def test_invoke_structured_decision_retries_until_valid(monkeypatch):
    calls: list[dict] = []
    responses = [
        "not valid json",
        '{"summary":"Structured ok","observations":["a"],"actions":[]}',
    ]

    async def _fake_call_ai(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(ai_mod, "call_ai", _fake_call_ai)
    decision, raw = asyncio.run(
        brain_mod._invoke_structured_decision(
            provider="openai",
            model="gpt-4o-mini",
            prompt="test",
            system_context="ctx",
            max_attempts=2,
        )
    )

    assert decision.summary == "Structured ok"
    assert raw.startswith("{")
    assert len(calls) == 2
    assert "response_schema" in calls[0]
    assert calls[0].get("fallback") is False
