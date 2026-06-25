# Container Reset Runbook

Use this when lifecycle strategy containers are split across stale SQLite/Chroma state and you need a clean restart.

## Scope

The reset script performs:

1. Backup `axiom.db` and `chromadb/`.
2. Delete all rows from:
   - `strategies`
   - `trades`
   - `portfolio_positions`
   - `backtest_result_trash`
   - `backtest_runs`
   - `strategy_events`
   - `strategy_candidates`
   - `backtest_results` (if present)
3. Reset `container_counters` prefixes `S`, `B`, `E`, `T` to `1`.
4. Wipe Chroma collection `backtest_results`.

## 1) Stop Running Services

Stop backend/frontend processes before reset to avoid write races.

## 2) Preview (Dry Run)

```powershell
.venv\Scripts\python.exe scripts\reset_strategy_containers.py
```

## 3) Execute Reset

```powershell
.venv\Scripts\python.exe scripts\reset_strategy_containers.py --yes
```

Optional custom backup location:

```powershell
.venv\Scripts\python.exe scripts\reset_strategy_containers.py --yes --backup-dir C:\tmp\axiom-reset-backup
```

## 4) Verify Acceptance Gate

Check DB table counts + counters:

```powershell
@'
from axiom.db import get_db
tables = [
    "strategies",
    "trades",
    "portfolio_positions",
    "backtest_result_trash",
    "backtest_runs",
    "strategy_events",
    "strategy_candidates",
    "backtest_results",
]
with get_db() as conn:
    for t in tables:
        try:
            c = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            print(f"{t}: {c}")
        except Exception as exc:
            print(f"{t}: <missing> ({exc})")
    rows = conn.execute("SELECT prefix, next_val FROM container_counters ORDER BY prefix").fetchall()
    print("container_counters:", [(r["prefix"], r["next_val"]) for r in rows])
'@ | .venv\Scripts\python.exe -
```

Check Chroma collection:

```powershell
@'
from axiom.vectordb import get_client
client = get_client()
names = sorted([c.name for c in client.list_collections()])
print("collections:", names)
if "backtest_results" in names:
    col = client.get_collection("backtest_results")
    print("backtest_results count:", col.count())
'@ | .venv\Scripts\python.exe -
```

Expected outcome:

- Reset tables report `0` rows.
- `container_counters` includes `('B', 1)`, `('E', 1)`, `('S', 1)`, `('T', 1)`.
- Chroma `backtest_results` is missing or has `0` items.

## 5) Restore From Backup (If Needed)

1. Stop services.
2. Copy backup DB file back to `AXIOM_HOME\axiom.db`.
3. Replace `AXIOM_HOME\chromadb` with the backup copy.
4. Restart services.
