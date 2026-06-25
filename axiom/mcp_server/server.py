# SPDX-FileCopyrightText: 2026 Judder <judder@forven.app> - 2026 srossitto79@gmail.com
# SPDX-License-Identifier: AGPL-3.0-or-later

"""MCP server that exposes the Axiom AI Drop Zone as tools.

FastMCP builds the JSON-Schema for each tool from the Python type hints +
docstring, so tool definitions are just annotated functions. The server
runs over stdio by default — ideal for Claude Desktop which spawns the
process and talks to it through pipes.

Tool naming: every tool is prefixed with `AXIOM_` so they do not collide
with other MCP servers the user has installed (common convention).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import AxiomClient

log = logging.getLogger("axiom.mcp_server")


_GATE_PATTERN = re.compile(
    r"(?P<id>Gate\d+|S\d+\s+REJECT|P\d+-\d+\s+REJECT|Hard sanity check failed|Trade count|IS Sharpe|Robustness|Max drawdown|Gauntlet missing|Insufficient [^:]+)",
    re.IGNORECASE,
)


def _metric_view(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Return the gate-relevant subset of a metrics object."""
    if not isinstance(metrics, dict):
        return {}
    keys = (
        "total_trades",
        "total_return_pct",
        "total_return",
        "sharpe",
        "profit_factor",
        "win_rate",
        "max_drawdown_pct",
        "max_drawdown",
        "robustness",
        "robustness_score",
        "gauntlet_score",
        "backtest_months",
        "trade_mode",
        "position_model",
    )
    return {key: metrics.get(key) for key in keys if key in metrics}


def _compact_backtest_payload(payload: Any) -> Any:
    """Strip bulky trades/equity curves while preserving gate evidence."""
    if not isinstance(payload, dict):
        return payload
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    in_sample = metrics.get("in_sample") if isinstance(metrics.get("in_sample"), dict) else {}
    out_of_sample = metrics.get("out_of_sample") if isinstance(metrics.get("out_of_sample"), dict) else metrics
    compact: dict[str, Any] = {
        "result_id": payload.get("result_id"),
        "strategy_id": payload.get("strategy_id"),
        "asset": payload.get("asset"),
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "status": payload.get("status"),
        "error": payload.get("error"),
        "bars": payload.get("bars"),
        "start_date": payload.get("start_date"),
        "end_date": payload.get("end_date"),
        "trade_mode": payload.get("trade_mode") or metrics.get("trade_mode"),
        "position_model": payload.get("position_model") or metrics.get("position_model"),
        "in_sample": _metric_view(in_sample),
        "out_of_sample": _metric_view(out_of_sample),
        "overall": _metric_view(metrics),
    }
    compact["trade_count"] = (
        compact["out_of_sample"].get("total_trades")
        if compact["out_of_sample"]
        else len(payload.get("trades") or [])
    )
    return {key: val for key, val in compact.items() if val not in (None, {}, [])}


def _parse_gate_failures(message: str | None) -> list[dict[str, Any]]:
    """Best-effort parser for legacy human gate messages."""
    text = str(message or "").strip()
    if not text:
        return []
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    failures = []
    for idx, part in enumerate(p.strip() for p in text.split(";") if p.strip()):
        match = _GATE_PATTERN.search(part)
        gate_id = (match.group("id") if match else f"gate_{idx + 1}").lower().replace(" ", "_")
        failures.append(
            {
                "id": gate_id,
                "message": part,
                "severity": "block" if "warn" not in part.lower() and "flag" not in part.lower() else "warning",
            }
        )
    return failures


def _with_gate_failures(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    error = payload.get("error") or payload.get("blocked_reason")
    failures = _parse_gate_failures(error)
    if failures:
        payload = dict(payload)
        payload["failed_gates"] = failures
        payload["passed"] = False
    elif payload.get("ok") is True:
        payload = dict(payload)
        payload["failed_gates"] = []
        payload["passed"] = True
    return payload


def build_server(client: AxiomClient | None = None) -> FastMCP:
    """Construct the FastMCP server instance with every tool registered.

    Exposed so tests can introspect the tool list without running stdio.
    """
    Axiom = client or AxiomClient()
    server = FastMCP(
        name="Axiom",
        instructions=(
            "axiom AI Drop Zone — create, register, and backtest trading "
            "strategies. Start by calling AXIOM_get_context to learn the "
            "system, then (optionally) AXIOM_create_session to group your "
            "work, write strategy .py files to the workspace, register them "
            "with AXIOM_register_strategy_file, and backtest with "
            "AXIOM_run_backtest. Use AXIOM_promote_strategy and "
            "AXIOM_get_gate_report to inspect lifecycle gates without "
            "bypassing them."
        ),
    )

    # ── Read-only tools ────────────────────────────────────────────────

    @server.tool(
        name="AXIOM_get_context",
        description=(
            "Return the full AI Drop Zone context: workspace path, strategy "
            "template, available datasets, API endpoints, canonical params, "
            "and the session workflow. Call this first in any new task."
        ),
    )
    def AXIOM_get_context() -> dict[str, Any]:
        return Axiom.get("/api/ai-dropzone/context")

    @server.tool(
        name="AXIOM_list_sessions",
        description=(
            "List recent AI Drop Zone sessions with strategy counts. Pass "
            "include_closed=false to see only sessions still open."
        ),
    )
    def AXIOM_list_sessions(limit: int = 20, include_closed: bool = True) -> dict[str, Any]:
        return Axiom.get(
            "/api/ai-dropzone/sessions",
            params={"limit": limit, "include_closed": str(include_closed).lower()},
        )

    @server.tool(
        name="AXIOM_get_session",
        description=(
            "Fetch a session's detail: tagged strategies and recent backtest "
            "runs. Use this to answer 'what did I try in session X?'."
        ),
    )
    def AXIOM_get_session(session_id: str) -> dict[str, Any]:
        return Axiom.get(f"/api/ai-dropzone/sessions/{session_id}")

    @server.tool(
        name="AXIOM_list_strategies",
        description=(
            "List registered strategies. Filter by status ('active', "
            "'archived', etc.). Use this to see what's in the lab."
        ),
    )
    def AXIOM_list_strategies(status: str | None = None) -> Any:
        return Axiom.get("/api/strategies", params={"status": status})

    @server.tool(
        name="AXIOM_get_recent_runs",
        description="Return the last N backtest runs across all strategies.",
    )
    def AXIOM_get_recent_runs(limit: int = 20) -> Any:
        return Axiom.get("/api/backtesting/runs", params={"limit": limit})

    @server.tool(
        name="AXIOM_get_result",
        description=(
            "Fetch a backtest result by result_id — metrics, trades, config. "
            "Use after AXIOM_run_backtest to inspect outcomes."
        ),
    )
    def AXIOM_get_result(result_id: str) -> Any:
        return Axiom.get(f"/api/results/{result_id}")

    @server.tool(
        name="AXIOM_get_gate_report",
        description=(
            "Return current strategy lifecycle state, latest compact backtest "
            "metrics, and the latest structured gate failure if one exists. "
            "This is read-only and does not attempt promotion."
        ),
    )
    def AXIOM_get_gate_report(strategy_id: str) -> dict[str, Any]:
        container = Axiom.get(f"/api/strategies/{strategy_id}/container")
        result_payload: Any = None
        try:
            results = Axiom.get("/api/results", params={"strategy": strategy_id, "limit": 1})
            rows = results.get("results") if isinstance(results, dict) else results
            if isinstance(rows, list) and rows:
                result_id = rows[0].get("result_id") or rows[0].get("id")
                result_payload = Axiom.get(f"/api/results/{result_id}") if result_id else rows[0]
        except Exception as exc:
            result_payload = {"error": f"Could not fetch latest result: {exc}"}
        # Strategy-scoped, structured gate checklist (the per-strategy
        # `/events` subroute does not exist; readiness is the purpose-built
        # source and gives structured pass/fail steps directly).
        readiness: Any = None
        try:
            readiness = Axiom.get(f"/api/lifecycle/strategies/{strategy_id}/readiness")
        except Exception as exc:
            readiness = {"error": f"Could not fetch readiness: {exc}"}
        failed_gates: list[dict[str, Any]] = []
        if isinstance(readiness, dict):
            for step in readiness.get("steps") or []:
                if isinstance(step, dict) and str(step.get("status")).lower() == "failed":
                    failed_gates.append(
                        {
                            "id": str(step.get("name") or "gate"),
                            "message": step.get("detail") or "",
                            "severity": "block",
                            "actionable": step.get("actionable"),
                        }
                    )
        return {
            "strategy_id": strategy_id,
            "strategy": container.get("strategy") if isinstance(container, dict) else container,
            "latest_result": _compact_backtest_payload(result_payload),
            "promotion_ready": readiness.get("ready") if isinstance(readiness, dict) else None,
            "failed_gates": failed_gates,
            "latest_gate_failure": failed_gates[0] if failed_gates else None,
        }

    @server.tool(
        name="AXIOM_get_quant_skills",
        description=(
            "Load curated quant insights for the current market regime. "
            "Check this before designing a strategy — past survivors leave "
            "hints here. regime options: 'trending', 'range_bound', 'volatile'."
        ),
    )
    def AXIOM_get_quant_skills(
        regime: str | None = None,
        skill_type: str | None = None,
        limit: int = 10,
        min_confidence: float = 0.5,
    ) -> Any:
        return Axiom.get(
            "/api/quant-skills",
            params={
                "regime": regime,
                "skill_type": skill_type,
                "limit": limit,
                "min_confidence": min_confidence,
            },
        )

    # ── Write tools ────────────────────────────────────────────────────

    @server.tool(
        name="AXIOM_create_session",
        description=(
            "Open a new AI Drop Zone session for grouping subsequent "
            "register-file and backtest calls. Returns an id like 'ADZ-0007' "
            "that you should pass as session_id in later tool calls."
        ),
    )
    def AXIOM_create_session(
        label: str = "",
        actor: str = "claude-mcp",
        objective: str = "",
    ) -> dict[str, Any]:
        return Axiom.post(
            "/api/ai-dropzone/sessions",
            {"label": label, "actor": actor, "objective": objective},
        )

    @server.tool(
        name="AXIOM_close_session",
        description="Mark an AI Drop Zone session closed (idempotent).",
    )
    def AXIOM_close_session(session_id: str) -> dict[str, Any]:
        return Axiom.post(f"/api/ai-dropzone/sessions/{session_id}/close")

    @server.tool(
        name="AXIOM_register_strategy_file",
        description=(
            "Register a single strategy .py file you wrote to the workspace. "
            "file_path must be an absolute path. Returns strategy_id and "
            "stage (quick_screen if certified, research_only otherwise). "
            "Pass session_id to tag the strategy to a session."
        ),
    )
    def AXIOM_register_strategy_file(
        file_path: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"file_path": file_path, "source": "ai_dropzone_mcp"}
        if session_id:
            body["session_id"] = session_id
        return Axiom.post("/api/strategies/intake/register-file", body)

    @server.tool(
        name="AXIOM_run_backtest",
        description=(
            "Run a backtest for a registered strategy on a dataset. "
            "dataset_id format: 'BTC/USDT-1h' (symbol-timeframe). "
            "parameters overrides the strategy's default_params. "
            "Pass session_id to tag the run to a session for later querying."
        ),
    )
    def AXIOM_run_backtest(
        strategy_id: str,
        dataset_id: str,
        session_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        timeframe: str | None = None,
        start: str | None = None,
        end: str | None = None,
        leverage: float | None = None,
        trade_mode: str | None = None,
        compact: bool = False,
    ) -> Any:
        body: dict[str, Any] = {
            "strategy_id": strategy_id,
            "dataset_id": dataset_id,
            "request_source": "mcp_server",
        }
        if session_id:
            body["session_id"] = session_id
        if parameters is not None:
            body["parameters"] = parameters
        if timeframe:
            body["timeframe"] = timeframe
        if start:
            body["start"] = start
        if end:
            body["end"] = end
        if leverage is not None:
            body["leverage"] = leverage
        if trade_mode:
            body["trade_mode"] = trade_mode
        result = Axiom.post("/api/backtesting/run", body)
        return _compact_backtest_payload(result) if compact else result

    @server.tool(
        name="AXIOM_create_strategy",
        description=(
            "Create a normal certified strategy container using a built-in "
            "execution family. This complements register-file for cases where "
            "the strategy should use an existing family such as rsi_momentum."
        ),
    )
    def AXIOM_create_strategy(
        hypothesis_id: str,
        strategy_type: str,
        symbol: str,
        timeframe: str,
        parameters: dict[str, Any],
        name: str = "",
    ) -> dict[str, Any]:
        return Axiom.post(
            "/api/backtesting/strategies",
            {
                "hypothesis_id": hypothesis_id,
                "type": strategy_type,
                "strategy_type": strategy_type,
                "symbol": symbol,
                "timeframe": timeframe,
                "params": parameters,
                "name": name,
            },
        )

    @server.tool(
        name="AXIOM_run_optimization",
        description=(
            "Run the normal optimization endpoint for a registered strategy. "
            "Use before gauntlet when parameter-search evidence is required."
        ),
    )
    def AXIOM_run_optimization(
        strategy_id: str,
        dataset_id: str,
        parameter_ranges: dict[str, Any] | None = None,
        objective: str | None = None,
        n_trials: int | None = None,
        timeframe: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"strategy_id": strategy_id, "dataset_id": dataset_id}
        if parameter_ranges is not None:
            body["parameter_ranges"] = parameter_ranges
        if objective:
            body["objective"] = objective
        if n_trials is not None:
            body["n_trials"] = n_trials
        if timeframe:
            body["timeframe"] = timeframe
        if start:
            body["start"] = start
        if end:
            body["end"] = end
        return Axiom.post("/api/backtesting/optimize", body)

    @server.tool(
        name="AXIOM_run_verdict",
        description=(
            "Run robustness/verdict tests for a strategy and dataset, such as "
            "walk_forward, parameter_stability, cost_stress, and monte_carlo."
        ),
    )
    def AXIOM_run_verdict(
        strategy_id: str,
        dataset_id: str,
        tests: list[str] | None = None,
    ) -> Any:
        body: dict[str, Any] = {"strategy_id": strategy_id, "dataset_id": dataset_id}
        if tests:
            body["tests"] = tests
        return Axiom.post("/api/backtesting/verdict/run", body)

    @server.tool(
        name="AXIOM_promote_strategy",
        description=(
            "Attempt a non-forced lifecycle promotion by default. Returns "
            "structured failed_gates when the real gate system blocks it."
        ),
    )
    def AXIOM_promote_strategy(
        strategy_id: str,
        to_status: str,
        from_status: str | None = None,
        reason: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"to_status": to_status, "reason": reason, "force": force}
        if from_status:
            body["from_status"] = from_status
        return _with_gate_failures(Axiom.post(f"/api/strategies/{strategy_id}/promote", body))

    @server.tool(
        name="AXIOM_get_paper_readiness",
        description=(
            "Read current state and latest evidence for paper readiness. This "
            "does not promote; use AXIOM_promote_strategy to attempt paper."
        ),
    )
    def AXIOM_get_paper_readiness(strategy_id: str) -> dict[str, Any]:
        report = AXIOM_get_gate_report(strategy_id)
        report["target_status"] = "paper"
        return report

    @server.tool(
        name="AXIOM_start_paper_session",
        description=(
            "Attempt to promote a gauntlet strategy to paper trading through "
            "the normal lifecycle gate. Defaults to non-forced."
        ),
    )
    def AXIOM_start_paper_session(
        strategy_id: str,
        reason: str = "MCP paper readiness promotion",
        force: bool = False,
    ) -> dict[str, Any]:
        return AXIOM_promote_strategy(
            strategy_id=strategy_id,
            from_status="gauntlet",
            to_status="paper",
            reason=reason,
            force=force,
        )

    @server.tool(
        name="AXIOM_run_gauntlet_candidate",
        description=(
            "Orchestrate a candidate evaluation: compact MCP backtest, optional "
            "verdict tests, then a non-forced gauntlet promotion attempt. "
            "This never bypasses gates unless force=true is explicitly passed."
        ),
    )
    def AXIOM_run_gauntlet_candidate(
        strategy_id: str,
        dataset_id: str,
        parameters: dict[str, Any] | None = None,
        trade_mode: str | None = None,
        session_id: str | None = None,
        run_verdict: bool = False,
        verdict_tests: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        backtest = AXIOM_run_backtest(
            strategy_id=strategy_id,
            dataset_id=dataset_id,
            session_id=session_id,
            parameters=parameters,
            trade_mode=trade_mode,
            compact=True,
        )
        verdict = None
        if run_verdict:
            verdict = AXIOM_run_verdict(strategy_id, dataset_id, verdict_tests)
        promotion = AXIOM_promote_strategy(
            strategy_id=strategy_id,
            from_status="quick_screen",
            to_status="gauntlet",
            reason="MCP gauntlet candidate evaluation",
            force=force,
        )
        return {"backtest": backtest, "verdict": verdict, "promotion": promotion}

    return server


def main() -> None:
    """Entry point: build the server and run it over stdio."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = build_server()
    server.run()  # defaults to stdio
