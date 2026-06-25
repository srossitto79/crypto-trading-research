# TOOLS.md — Local Notes & Runtime

## How Axiom Actually Runs

Axiom is a **Tauri desktop app**. The Rust shell spawns the embedded Python backend as `python -m axiom.api`. **There are no 24/7 OS services, no systemd units, no cron jobs, no scheduled tasks.** Everything is gated on the app being open:

- Open the app → the FastAPI backend starts and, under a single runtime-worker lock, runs all loops **in-process**:
  - **scheduler loop** (~30s tick — cron/interval job dispatcher)
  - **headless agent loop** (~5s)
  - **headless brain loop** (~20s)
  - **data/risk daemon** (price/data ingest + hard kill-switch, daily-loss halt, position reconcile, heartbeat)
- Close the app → all of it stops. Missed cycles are **collapsed into one catch-up run** on reopen (not replayed N times).

Do **not** try to `systemctl`, `sc start`, or otherwise "start a service" — there are none, and on Windows those commands don't apply. To run Axiom, open the app (or, for dev, `start_all.ps1`).

## Endpoints (local only)

| What | Address |
|------|---------|
| Backend API (FastAPI/uvicorn) | `http://127.0.0.1:8003` |
| Frontend dev server (SvelteKit/Vite) | `http://127.0.0.1:5173` |

## Dev-only entry points

These exist for development; the packaged app does not use them:

- `start_all.ps1` — full local bootstrap (backend + frontend, optionally the Discord bot when `START_BOT=1` and a token is configured, and the standalone daemon when `START_DAEMON=1`).
- `python -m axiom daemon start` — run the data/risk daemon standalone.
- `python -m axiom bot start` — run the optional Discord bot (legacy).

## What Goes Here

Environment-specific notes worth keeping: device details, local ports, anything unique to this machine. This is your cheat sheet — add whatever helps you do the job.
