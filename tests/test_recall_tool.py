"""Phase 1 (P1-T10) — recall_similar_situation tool registration tests.

Asserts:
- Tool is exposed to the Brain agent only (other agents can't see it).
- Tool returns a valid JSON envelope on success.
- Tool returns an envelope on missing query rather than raising.
- Tool clamps limit to [1, 20].
"""
from __future__ import annotations

import json

from axiom.agents.tool_registry import get_tools_for_agent
from axiom.db import get_db


def _ensure_agent(agent_id: str, name: str, role: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, name, role) VALUES (?, ?, ?)",
            (agent_id, name, role),
        )


def test_recall_tool_visible_to_brain(AXIOM_db):
    _ensure_agent("brain", "Brain", "brain")
    names = {t["name"] for t in get_tools_for_agent("brain")}
    assert "recall_similar_situation" in names


def test_recall_tool_hidden_from_other_agents(AXIOM_db):
    _ensure_agent("quant-researcher", "Quant Researcher", "quant-researcher")
    names = {t["name"] for t in get_tools_for_agent("quant-researcher")}
    assert "recall_similar_situation" not in names


def test_recall_tool_returns_envelope_on_success(AXIOM_db, monkeypatch):
    from axiom.agents import tools_brain  # noqa: F401 — registers tool

    # Patch the underlying recall to avoid hitting any real LLMs.
    fake_result = {
        "summary": "synthesized pattern",
        "hits": [{"source": "brain_decisions", "id": 1, "score": 0.0,
                  "snippet": "snip", "situation": "BTC breakout",
                  "outcome": None, "created_at": None,
                  "deep_link_url": "/brain/decisions/1"}],
        "aux_model": "openrouter:openai/gpt-4o-mini",
        "latency_ms": 12,
    }

    import axiom.agents.tools_brain as tb
    monkeypatch.setattr(
        "axiom.recall.recall_similar_situation",
        lambda *a, **kw: fake_result,
    )

    raw = tb._tool_recall_similar_situation({"query": "BTC breakout", "scope": "decisions", "limit": 3})
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["summary"] == "synthesized pattern"
    assert payload["hits"][0]["id"] == 1
    assert payload["aux_model"] == "openrouter:openai/gpt-4o-mini"


def test_recall_tool_missing_query_returns_envelope(AXIOM_db):
    import axiom.agents.tools_brain as tb

    raw = tb._tool_recall_similar_situation({"query": ""})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == "missing_query"


def test_recall_tool_clamps_limit_high(AXIOM_db, monkeypatch):
    captured: dict = {}

    def spy(query, scope="all", limit=5):
        captured["limit"] = limit
        return {"summary": "", "hits": [], "aux_model": None, "latency_ms": 0}

    monkeypatch.setattr("axiom.recall.recall_similar_situation", spy)

    import axiom.agents.tools_brain as tb
    tb._tool_recall_similar_situation({"query": "x", "limit": 9999})
    assert captured["limit"] == 20


def test_recall_tool_clamps_limit_low(AXIOM_db, monkeypatch):
    captured: dict = {}

    def spy(query, scope="all", limit=5):
        captured["limit"] = limit
        return {"summary": "", "hits": [], "aux_model": None, "latency_ms": 0}

    monkeypatch.setattr("axiom.recall.recall_similar_situation", spy)

    import axiom.agents.tools_brain as tb
    tb._tool_recall_similar_situation({"query": "x", "limit": 0})
    assert captured["limit"] == 1


def test_recall_tool_normalizes_unknown_scope_to_all(AXIOM_db, monkeypatch):
    captured: dict = {}

    def spy(query, scope="all", limit=5):
        captured["scope"] = scope
        return {"summary": "", "hits": [], "aux_model": None, "latency_ms": 0}

    monkeypatch.setattr("axiom.recall.recall_similar_situation", spy)

    import axiom.agents.tools_brain as tb
    tb._tool_recall_similar_situation({"query": "x", "scope": "nonsense"})
    assert captured["scope"] == "all"


def test_recall_tool_handles_underlying_exception(AXIOM_db, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("db wedged")

    monkeypatch.setattr("axiom.recall.recall_similar_situation", boom)

    import axiom.agents.tools_brain as tb
    raw = tb._tool_recall_similar_situation({"query": "x"})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == "recall_failed"
