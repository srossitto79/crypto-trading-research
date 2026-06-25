"""axiom MCP server.

Exposes the AI Drop Zone HTTP API as Model Context Protocol tools so a
Claude Desktop (or any MCP-compatible client) can drive the strategy
generation + backtest loop directly — no copy-paste.

Run with:
    python -m axiom.mcp_server

The Axiom backend must be running (the stdio server is a thin HTTP client of
the local API). `command` must be the project's venv interpreter, and unless
`Axiom` is pip-installed, PYTHONPATH must point at the repo root so
`-m axiom.mcp_server` resolves regardless of the client's working directory.

Configuration via environment:
    AXIOM_API_URL        default http://127.0.0.1:8003
    AXIOM_API_KEY        sent as x-api-key (only needed if AXIOM_AUTH_REQUIRED)
    AXIOM_OPERATOR_KEY   sent as x-operator-key (only needed if auth is on)
    AXIOM_MCP_TIMEOUT    HTTP timeout in seconds (default 60)

Claude Desktop config snippet (~/Library/Application Support/Claude/
claude_desktop_config.json or %APPDATA%/Claude/claude_desktop_config.json).
Use the ABSOLUTE venv python path and set PYTHONPATH to the repo root; the API
keys are optional (omit them unless auth is enabled):

    {
      "mcpServers": {
        "Axiom": {
          "command": "<repo>/.venv/Scripts/python.exe",
          "args": ["-m", "axiom.mcp_server"],
          "env": {
            "PYTHONPATH": "<repo>",
            "AXIOM_API_URL": "http://127.0.0.1:8003"
          }
        }
      }
    }
"""

from .server import build_server, main

__all__ = ["build_server", "main"]
