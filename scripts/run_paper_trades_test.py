"""Run scanner in high_activity_test mode to generate paper trades for new strategies."""
import sys, json, time
sys.path.insert(0, '.')


if __name__ == '__main__':
    import sqlite3
    from axiom.config import AXIOM_DB

    # Enable high_activity_test mode
    print("Starting paper service in high_activity_test mode...")
    from axiom.api_domains.paper import start_paper_service, stop_paper_service, _run_scanner_once
    result = start_paper_service(high_activity_test=True, run_scan_now=False)
    print(f"  Service started: {result.get('status')}")

    # Run scanner 3 times
    for i in range(3):
        print(f"\nScan {i+1}/3...")
        ok, err = _run_scanner_once(execute_positions=True)
        print(f"  ok={ok} err={err}")
        time.sleep(2)

    # Check for new trades
    conn = sqlite3.connect(AXIOM_DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT strategy_id, symbol, direction, pnl, status, execution_type, created_at
        FROM trades WHERE strategy_id IN ('S03096', 'S03097')
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()
    print(f"\nTrades for S03096/S03097: {len(rows)}")
    for r in rows:
        print(f"  {r['strategy_id']} {r['symbol']} {r['direction']} pnl={r['pnl']} status={r['status']} {r['created_at']}")

    # Also check S00945 and all paper types
    rows2 = conn.execute("""
        SELECT strategy_id, symbol, direction, pnl, status, created_at
        FROM trades ORDER BY created_at DESC LIMIT 15
    """).fetchall()
    print(f"\nAll recent trades ({len(rows2)}):")
    for r in rows2:
        print(f"  {r['strategy_id']} {r['symbol']} {r['direction']} pnl={r['pnl']} status={r['status']} {r['created_at']}")

    conn.close()

    # Stop high_activity_test
    stop_paper_service()
    print("\nStopped high_activity_test mode.")
    print("Done.")
