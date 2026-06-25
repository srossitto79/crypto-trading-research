"""Phase 1 — Brain memory tool registration tests (P1-T03).

Asserts the `memory` tool is exposed to the Brain agent only and that all four
actions (`view`, `add`, `replace`, `remove`) round-trip through the tool
adapter, with cap violations returned as a structured error envelope rather
than raised.
"""
from __future__ import annotations

import importlib
import json


def _load_tools_brain():
    # Import for side-effect: register_tool decorator populates the registry.
    return importlib.import_module("axiom.agents.tools_brain")


def test_memory_tool_registered_for_brain(AXIOM_db):
    _load_tools_brain()
    from axiom.agents.tool_registry import get_tools_for_agent

    names = {tool["name"] for tool in get_tools_for_agent("brain")}
    assert "memory" in names


def test_memory_tool_not_visible_to_quant_agents(AXIOM_db):
    _load_tools_brain()
    from axiom.agents.tool_registry import get_tools_for_agent

    for non_brain in (
        "quant-researcher",
        "strategy-developer",
        "execution-trader",
        "risk-manager",
    ):
        names = {tool["name"] for tool in get_tools_for_agent(non_brain)}
        assert "memory" not in names, f"memory tool leaked to {non_brain}"


def test_memory_tool_view_returns_initial_state(AXIOM_db):
    tools_brain = _load_tools_brain()
    out = json.loads(tools_brain._tool_memory({"action": "view"}))
    assert out["ok"] is True
    assert out["body"] == ""
    assert out["char_count"] == 0
    assert out["cap"] == 2000


def test_memory_tool_add_then_view_roundtrip(AXIOM_db, monkeypatch):
    tools_brain = _load_tools_brain()
    monkeypatch.setattr(
        tools_brain,
        "_current_agent_id_var",
        type("_Var", (), {"get": staticmethod(lambda: "brain")})(),
        raising=False,
    )
    add_out = json.loads(tools_brain._tool_memory({"action": "add", "content": "first note"}))
    assert add_out["ok"] is True
    view_out = json.loads(tools_brain._tool_memory({"action": "view"}))
    assert view_out["body"] == "first note"


def test_memory_tool_replace_overwrites(AXIOM_db):
    tools_brain = _load_tools_brain()
    tools_brain._tool_memory({"action": "add", "content": "old"})
    tools_brain._tool_memory({"action": "replace", "content": "new body"})
    view_out = json.loads(tools_brain._tool_memory({"action": "view"}))
    assert view_out["body"] == "new body"


def test_memory_tool_remove_strips_substring(AXIOM_db):
    tools_brain = _load_tools_brain()
    tools_brain._tool_memory({"action": "replace", "content": "alpha-beta-gamma"})
    rm = json.loads(tools_brain._tool_memory({"action": "remove", "needle": "beta-"}))
    assert rm["ok"] is True
    view_out = json.loads(tools_brain._tool_memory({"action": "view"}))
    assert view_out["body"] == "alpha-gamma"


def test_memory_tool_remove_missing_needle(AXIOM_db):
    tools_brain = _load_tools_brain()
    tools_brain._tool_memory({"action": "replace", "content": "alpha"})
    rm = json.loads(tools_brain._tool_memory({"action": "remove", "needle": "zzz"}))
    assert rm == {"ok": False, "reason": "not_found"}


def test_memory_tool_cap_violation_returns_envelope(AXIOM_db):
    tools_brain = _load_tools_brain()
    out = json.loads(
        tools_brain._tool_memory({"action": "replace", "content": "x" * 2001})
    )
    assert out["ok"] is False
    assert out["error"] == "memory_cap_exceeded"
    assert out["attempted_chars"] == 2001
    assert out["cap"] == 2000


def test_memory_tool_invalid_action(AXIOM_db):
    tools_brain = _load_tools_brain()
    out = json.loads(tools_brain._tool_memory({"action": "noop"}))
    assert out["ok"] is False
    assert out["error"] == "invalid_action"


def test_memory_tool_add_missing_content_rejected(AXIOM_db):
    tools_brain = _load_tools_brain()
    out = json.loads(tools_brain._tool_memory({"action": "add"}))
    assert out["ok"] is False
    assert out["error"] == "missing_content"


def test_memory_tool_remove_missing_needle_param_rejected(AXIOM_db):
    tools_brain = _load_tools_brain()
    out = json.loads(tools_brain._tool_memory({"action": "remove"}))
    assert out["ok"] is False
    assert out["error"] == "missing_needle"
