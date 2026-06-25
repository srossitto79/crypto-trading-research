# SPDX-FileCopyrightText: 2026 Judder <judder@forven.app> - 2026 srossitto79@gmail.com
# SPDX-License-Identifier: AGPL-3.0-or-later

"""axiom agent harness — drive the running Axiom backend over its HTTP API.

This is the transport-neutral way for ANY AI harness (Claude Code, Codex, a
Tauri sidecar, CI, a plain script) to use Axiom without the MCP server. The
MCP server (`axiom.mcp_server`) is itself just a thin stdio wrapper over the
same `:8003` REST API this client targets; everything the MCP can do, this can
do, with zero third-party dependencies (stdlib `urllib` only).

Quick start:
    from axiom.agent import AxiomAgentClient
    fc = AxiomAgentClient()              # defaults to http://127.0.0.1:8003
    print(fc.health())
    res = fc.run_backtest("S02545", "BTC/USDT-1h")
    print(AxiomAgentClient.metrics(res))

Or from a shell (great for Claude Code / Codex):
    python -m axiom.agent health
    python -m axiom.agent backtest --strategy S02545 --dataset BTC/USDT-1h --compact
"""

from .client import AxiomAgentClient, AxiomAPIError, QUICK_SCREEN_THRESHOLDS

__all__ = ["AxiomAgentClient", "AxiomAPIError", "QUICK_SCREEN_THRESHOLDS"]
