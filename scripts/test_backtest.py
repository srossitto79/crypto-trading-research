import json
import logging
from uuid import uuid4
from axiom.db import init_db, get_db
from axiom.strategies.backtest import backtest_strategy

logging.basicConfig(level=logging.INFO)

def test_backtest_persistence():
    init_db()
    strategy_id = str(uuid4())
    params = {"kc_period": 20, "kc_mult": 2.0}
    
    print(f"Running backtest for strategy {strategy_id}...")
    result = backtest_strategy(strategy_id, "ETH", "keltner", params, bars=1440)
    
    metrics = result.get("metrics", {})
    if "error" in result:
        print(f"Backtest error: {result['error']}")
        return

    print("Returned metrics structure:")
    print(json.dumps(metrics, indent=2)[:500] + "...\n")
    
    # Verify in DB
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE strategy_id = ?", 
            (strategy_id,)
        ).fetchone()
        
    assert row is not None, "Backtest run not persisted to database!"
    is_metrics = json.loads(row["is_metrics_json"])
    oos_metrics = json.loads(row["oos_metrics_json"])
    
    print(f"Verified persistence in DB: {row['run_id']}")
    print(f"IS Trades: {is_metrics.get('total_trades')} | OOS Trades: {oos_metrics.get('total_trades')}")
    print(f"Robustness Score: {row['robustness_score']}")

if __name__ == "__main__":
    test_backtest_persistence()
