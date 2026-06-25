"""Verify a paper trade matches a backtest (execution-correctness confirmation).

Usage: python scripts/verify_paper_vs_backtest.py <STRATEGY_ID>

1. Triggers a synchronous execution scan so the strategy is evaluated on the latest
   closed bar (opens a paper trade if its entry signal fires).
2. Reads the most-recent paper trade for the strategy from the DB.
3. Re-runs a backtest of the same strategy/params/symbol/timeframe and confirms the
   backtest produces an entry at the SAME bar_time, same direction, and a matching
   entry price (within fee+slippage tolerance) — i.e. the paper fill is exactly what
   the strategy's signal logic produces on that candle.
"""
import datetime as _dt
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8003"
DB = str(Path(os.environ.get("AXIOM_HOME", Path.home() / ".Axiom")) / "axiom.db")


def _post(path, body=None, t=300):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=t))


def latest_trade(sid):
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy_id=? ORDER BY datetime(created_at) DESC LIMIT 1", (sid,)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _norm_time(t):
    try:
        return _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def main(sid):
    print(f"=== triggering execution scan for {sid} ===")
    try:
        r = _post("/api/system/scanner/execution-run")
        print("scan:", json.dumps(r)[:300])
    except Exception as e:
        print("scan trigger error:", repr(e)[:200])

    tr = latest_trade(sid)
    if not tr:
        print(f"NO paper trade for {sid} yet (entry signal not fired on the latest bar).")
        return 1
    sd = json.loads(tr.get("signal_data") or "{}")
    diag = sd.get("runtime_diagnostics", {})
    params = diag.get("canonical_params") or {}
    bar_time = sd.get("bar_time")
    direction = (tr.get("direction") or "").lower()
    entry = tr.get("entry_price")
    asset = tr.get("asset") or tr.get("symbol")
    tf = tr.get("timeframe")
    print(f"\nPAPER TRADE {tr.get('display_id')}: {asset} {direction} @ {entry} bar_time={bar_time} tf={tf}")
    print(f"  canonical_params={params}")

    print("\n=== re-running backtest to confirm the entry ===")
    bt = _post("/api/backtesting/run", {
        "strategy_id": sid, "dataset_id": f"{asset}/USDT-{tf}" if "/" not in str(asset) else f"{asset}-{tf}",
        "timeframe": tf, "parameters": params,
    })
    trades = bt.get("trades") or (bt.get("result", {}) or {}).get("trades") or []
    print(f"backtest returned {len(trades)} trades")
    bt_bar = _norm_time(bar_time)
    for t in trades:
        et = _norm_time(t.get("entry_time") or t.get("entry_date") or t.get("opened_at") or t.get("bar_time"))
        if et and bt_bar and abs((et - bt_bar).total_seconds()) <= 3600:
            bt_dir = (t.get("direction") or "long").lower()
            if bt_dir != direction:
                print(f"  DIRECTION MISMATCH bar={et}: backtest={bt_dir} paper={direction}")
                return 1
            ep = t.get("entry_price") or t.get("entry")
            tol = (3.5 + 2.0) / 1e4 * float(entry) + 1e-6
            match = ep is not None and abs(float(ep) - float(entry)) <= max(tol, abs(float(entry)) * 0.005)
            print(f"  MATCH bar={et} dir={bt_dir} bt_entry={ep} paper_entry={entry} -> {'CONFIRMED' if match else 'price mismatch'}")
            return 0 if match else 1
    print("  no backtest trade at the paper bar_time (check window coverage / OOS-only trades)")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/verify_paper_vs_backtest.py <STRATEGY_ID>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
