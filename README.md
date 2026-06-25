# Axiom

**Axiom is a free, open-source, self-hosted autonomous crypto-trading research & operations workspace.** It pairs a team of AI agents with a backtesting engine, a robustness "gauntlet," paper trading, and an operator dashboard — so idea generation, validation, and monitoring all live in one repo you run on your own machine.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

> ⚠️ **Paper + testnet by default. Not financial advice.**
> Axiom ships in **paper-trading mode**. A live/mainnet execution engine exists in the code but is **unsupported, disabled by default, and reachable only via deliberate opt-in** — enabling real-money trading is entirely at your own risk. Trading crypto carries a substantial risk of total loss; backtest and paper results do not predict live performance. See [`DISCLAIMER.md`](DISCLAIMER.md).

## What it does

- **Autonomous research agents** — a strategy-developer / quant-researcher / risk / execution agent team that generates hypotheses, builds strategies, and runs them through validation.
- **Backtesting engine** — bar-by-bar execution with vectorized signal generation.
- **The Gauntlet** — a robustness battery (walk-forward analysis, Monte-Carlo, parameter-jitter, cost-stress) that gates strategy promotion.
- **Paper trading** with real risk controls: stop-losses, a drawdown kill-switch, fill reconciliation, and liquidation-distance monitoring.
- **Operator dashboard** (SvelteKit) plus an in-process **MCP** server, so you can also drive it from Claude, Codex, and other MCP clients.
- **Bring your own keys** — your LLM provider key and your exchange credentials stay **local**; nothing is sent to any external server. There is no account and no sign-up.

## Stack

- **Backend:** Python 3.11+, FastAPI, uvicorn
- **Frontend:** SvelteKit 2, Svelte 5, Tailwind CSS, Vite
- **Database:** SQLite under `AXIOM_HOME` (defaults to `~/.axiom`)
- **Memory:** ChromaDB
- **Exchange layer:** CCXT adapters under `axiom/exchange/` (Hyperliquid, Binance, Kraken, OKX, Coinbase, and any CCXT-supported exchange)
- **Default local URLs:** frontend `http://127.0.0.1:5173` · backend `http://127.0.0.1:8003` · health `http://127.0.0.1:8003/api/health`

## Quick Start

Requirements: **Python 3.11+**, **Node.js**, and **git**.

```bash
git clone https://github.com/srossitto79/axiom.git
cd axiom
```

### Docker (recommended)

```bash
docker-compose up -d
```

Then open **http://127.0.0.1:5173**. On first run the setup wizard walks you through connecting an exchange and an LLM provider.

### Windows (manual)

```powershell
$env:START_BOT = '0'
$env:START_DAEMON = '0'
powershell -ExecutionPolicy Bypass -File .\start_all.ps1
```

### macOS / Linux (manual)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cd frontend && npm install && cd ..
cp .env.example .env
START_BOT=0 START_DAEMON=0 bash start_all.sh
```

### Bring your own keys

Axiom uses **your** LLM provider and **your** exchange — configured locally and never sent anywhere. Connect an LLM provider in the dashboard under **Settings → Agents**, or export the provider env var before starting (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Add exchange credentials in **Settings → Trading**.

## Documentation

In-repo: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/FIRST_RUN_CHECKLIST.md`](docs/FIRST_RUN_CHECKLIST.md) · [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md)

## Common commands

```bash
# Full stack (Docker)
docker-compose up -d
docker-compose logs -f axiom-backend

# Full stack (local)
powershell -ExecutionPolicy Bypass -File .\start_all.ps1     # Windows
bash start_all.sh                                            # macOS / Linux

# Backend only
python -m uvicorn --app-dir . axiom.api:app --host 127.0.0.1 --port 8003 --reload

# Frontend only
cd frontend && npm run dev

# CLI
python -m axiom --help

# Tests / checks
python -m pytest tests -q
python -m ruff check axiom tests
cd frontend && npm test && npm run check
```

## Configuration & safety

Start from `.env.example` and set only what you need. Key options:

- `AXIOM_EXECUTION_MODE=paper` — the default. `live` exists but is unsupported; requires `AXIOM_ALLOW_MAINNET=1` and mainnet credentials, entirely at your own risk.
- `START_BOT=0` / `START_DAEMON=0` — leave the Discord bot / autonomous daemon off unless you want them.
- `AXIOM_BIND_HOST=127.0.0.1` — localhost only by default. Exposing the API requires `AXIOM_API_KEY`.
- `AXIOM_API_KEY` / `AXIOM_OPERATOR_KEY` — required if the API is reachable beyond localhost.
- `AXIOM_HOME` — to put runtime state outside the default `~/.axiom`.

Never commit `.env`, `*.db`, auth tokens, or API keys (they're gitignored).

## Fork notes

Axiom is a personal fork of [Forven](https://github.com/judder659/forven) by Judder, maintained by [Salvatore Rossitto](https://github.com/srossitto79). The following changes have been made from the upstream Forven codebase:

- **Renamed** the project and Python package from `forven` → `axiom` throughout (package, Docker images, volume names, config paths).
- **Docker migration** — added automatic detection and rename of `forven.db` → `axiom.db` inside a mounted `AXIOM_HOME` volume on first boot, so existing data survives the rename without manual intervention.
- **Multi-exchange CCXT layer** — added full CCXT adapter support for Binance, Kraken, OKX, Coinbase, and any generic CCXT-supported exchange, alongside the existing Hyperliquid integration.
- **Setup wizard** — multi-exchange setup wizard that dynamically shows credentials for the selected exchange; wizard now auto-saves settings when advancing steps.
- **API routing** — middleware now handles both `/api/Axiom/` and `/api/axiom/` (case-insensitive) path prefixes so the frontend works correctly after the rename.
- **BOM handling** — strategy files and the AST security guard now handle UTF-8 BOM characters, fixing a Windows editor compatibility issue that was silently rejecting all custom strategy modules.
- **Bug fixes** — `Signal.HOLD` / `Signal.LONG` / `Signal.SHORT` sentinels added to the `Signal` dataclass; fixed a `NameError` in the update-check endpoint caused by the package rename.

## Contributing

This is a personal fork — external pull requests are not currently accepted. Issues and discussion are welcome via [GitHub Issues](https://github.com/srossitto79/axiom/issues).

Found a security issue? See [`SECURITY.md`](SECURITY.md).

## License

Axiom is a fork of Forven. Original copyright: Copyright (C) 2026 Judder.
Fork modifications: Copyright (C) 2026 Salvatore Rossitto <srossitto79@gmail.com>

Licensed under the **GNU Affero General Public License v3 or later**. Full text in [`LICENSE`](LICENSE); see also [`NOTICE`](NOTICE) and [`DISCLAIMER.md`](DISCLAIMER.md).
