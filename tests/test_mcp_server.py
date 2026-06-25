"""Tests for the Axiom MCP server.

These do not spawn a backend — they verify that the FastMCP server builds,
registers every expected tool, and that each tool has a non-empty
description (MCP clients display this to users, so a blank description is
a bug). For end-to-end smoke of the HTTP layer, see the README snippet —
that requires a live backend.
"""

from __future__ import annotations

import asyncio


from axiom.mcp_server.server import build_server


EXPECTED_TOOL_NAMES = {
    "AXIOM_get_context",
    "AXIOM_list_sessions",
    "AXIOM_get_session",
    "AXIOM_list_strategies",
    "AXIOM_get_recent_runs",
    "AXIOM_get_result",
    "AXIOM_get_gate_report",
    "AXIOM_get_quant_skills",
    "AXIOM_create_session",
    "AXIOM_close_session",
    "AXIOM_register_strategy_file",
    "AXIOM_run_backtest",
    "AXIOM_create_strategy",
    "AXIOM_run_optimization",
    "AXIOM_run_verdict",
    "AXIOM_promote_strategy",
    "AXIOM_get_paper_readiness",
    "AXIOM_start_paper_session",
    "AXIOM_run_gauntlet_candidate",
}


def _list_tool_names() -> list[str]:
    server = build_server()
    tools = asyncio.run(server.list_tools())
    return [t.name for t in tools]


def test_build_server_registers_expected_tools():
    names = set(_list_tool_names())
    assert names == EXPECTED_TOOL_NAMES, f"missing: {EXPECTED_TOOL_NAMES - names}; unexpected: {names - EXPECTED_TOOL_NAMES}"


def test_all_tools_have_descriptions():
    server = build_server()
    tools = asyncio.run(server.list_tools())
    for t in tools:
        assert t.description and t.description.strip(), f"tool {t.name} has no description"


def test_tools_namespaced_with_AXIOM_prefix():
    for name in _list_tool_names():
        assert name.startswith("AXIOM_"), f"tool {name} missing AXIOM_ prefix — will collide with other MCP servers"


def test_register_strategy_file_schema_has_required_file_path():
    server = build_server()
    tools = asyncio.run(server.list_tools())
    reg = next(t for t in tools if t.name == "AXIOM_register_strategy_file")
    schema = reg.inputSchema
    assert schema.get("type") == "object"
    props = schema.get("properties", {})
    assert "file_path" in props
    # session_id is optional
    assert "session_id" in props


def test_run_backtest_schema_exposes_session_id():
    server = build_server()
    tools = asyncio.run(server.list_tools())
    bt = next(t for t in tools if t.name == "AXIOM_run_backtest")
    props = bt.inputSchema.get("properties", {})
    assert "strategy_id" in props
    assert "dataset_id" in props
    assert "session_id" in props
    assert "compact" in props


def test_lifecycle_tools_exposed():
    server = build_server()
    tools = asyncio.run(server.list_tools())
    by_name = {t.name: t for t in tools}
    for name in [
        "AXIOM_run_verdict",
        "AXIOM_promote_strategy",
        "AXIOM_get_gate_report",
        "AXIOM_start_paper_session",
        "AXIOM_run_gauntlet_candidate",
    ]:
        assert name in by_name
