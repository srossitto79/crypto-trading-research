"""Tool-execution ledger prepended to agent task outputs.

Guards against false success reports: the LLM occasionally claims it created
a strategy or hypothesis when the underlying tool errored. The ledger queries
task_audit_log for ground truth and exposes it at the top of the task
narrative so operators can cross-check claims.
"""
from __future__ import annotations

from axiom.agents.runner import _build_tool_ledger
from axiom.db import log_tool_call


def test_ledger_empty_for_unknown_task(AXIOM_db):
    text, trace = _build_tool_ledger("T99999")
    assert text == ""
    assert trace == []


def test_ledger_empty_for_blank_task_id(AXIOM_db):
    text, trace = _build_tool_ledger("")
    assert text == ""
    assert trace == []


def test_ledger_marks_artifact_tool_failure(AXIOM_db):
    task_id = "T00123"
    # Simulate a create_strategy that errored (no "ok":true in output).
    log_tool_call(
        task_id,
        agent_id="strategy-developer",
        tool_name="create_strategy",
        input_data={"type": "rsi_atr_regime_momentum"},
        output_summary="Error creating strategy: runtime type has no registered class",
        duration_ms=42,
    )

    text, trace = _build_tool_ledger(task_id)

    assert "TOOL EXECUTION LEDGER" in text
    assert "[FAILED] create_strategy" in text
    assert len(trace) == 1
    assert trace[0]["tool_name"] == "create_strategy"
    assert trace[0]["ok"] is False


def test_ledger_marks_artifact_tool_success(AXIOM_db):
    task_id = "T00124"
    log_tool_call(
        task_id,
        agent_id="strategy-developer",
        tool_name="create_hypothesis",
        input_data={"title": "h"},
        output_summary='{"ok": true, "hypothesis_id": "HYP-001"}',
        duration_ms=33,
    )

    text, trace = _build_tool_ledger(task_id)

    assert "[ok] create_hypothesis" in text
    assert "[FAILED]" not in text
    assert trace[0]["ok"] is True


def test_ledger_warns_when_no_artifact_tools_ran(AXIOM_db):
    """Read-only tools only => ledger flags unverified narrative claims."""
    task_id = "T00125"
    log_tool_call(
        task_id,
        agent_id="research-agent",
        tool_name="read_file",
        input_data={"path": "foo.py"},
        output_summary="file contents",
        duration_ms=5,
    )
    log_tool_call(
        task_id,
        agent_id="research-agent",
        tool_name="search_memory",
        input_data={"q": "x"},
        output_summary="[]",
        duration_ms=12,
    )

    text, trace = _build_tool_ledger(task_id)

    assert "No artifact-producing tools" in text
    assert "treat those claims as unverified" in text
    assert "2 read-only" in text
    # Both auxiliary calls still show up in the structured trace.
    names = sorted(t["tool_name"] for t in trace)
    assert names == ["read_file", "search_memory"]


def test_ledger_separates_artifact_from_auxiliary(AXIOM_db):
    task_id = "T00126"
    log_tool_call(task_id, "r", "read_file", {}, "ok", 1)
    log_tool_call(task_id, "r", "create_strategy", {},
                  'Strategy created: S00001', 2)
    log_tool_call(task_id, "r", "search_chroma", {}, "[]", 1)

    text, trace = _build_tool_ledger(task_id)

    assert "[ok] create_strategy" in text
    assert "Artifact-producing tool calls:" in text
    assert "2 read-only" in text
    assert len(trace) == 3
