"""Trigger one paper trading scanner cycle to generate signals for new paper strategies."""
import sys, json
sys.path.insert(0, '.')


if __name__ == '__main__':
    import sqlite3
    from axiom.config import AXIOM_DB

    conn = sqlite3.connect(AXIOM_DB)
    conn.row_factory = sqlite3.Row

    # Check paper strategies
    rows = conn.execute("""
        SELECT id, type, symbol, timeframe FROM strategies WHERE status='paper'
    """).fetchall()
    print("Paper strategies:")
    for r in rows:
        print(f"  {r['id']} {r['type']} {r['symbol']}/{r['timeframe']}")

    conn.close()

    # Trigger scanner
    print("\nTriggering paper scanner scan...")
    try:
        from axiom.api_domains.paper import _run_scanner_once
        ok, err = _run_scanner_once(execute_positions=True)
        print(f"Scanner result: ok={ok} err={err}")
    except Exception as e:
        print(f"Scanner error: {e}")
        import traceback
        traceback.print_exc()

    # Check for new trades
    conn2 = sqlite3.connect(AXIOM_DB)
    conn2.row_factory = sqlite3.Row
    rows = conn2.execute("""
        SELECT strategy_id, symbol, direction, pnl, status, execution_type, created_at
        FROM trades WHERE strategy_id IN ('S03096', 'S03097')
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()
    print(f"\nNew trades for S03096/S03097: {len(rows)}")
    for r in rows:
        print(f"  {r['strategy_id']} {r['symbol']} {r['direction']} pnl={r['pnl']} status={r['status']} {r['created_at']}")

    conn2.close()
    print("\nDone.")
