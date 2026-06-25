import os
import json
import logging
from uuid import uuid4
from axiom.db import init_db, get_db
from axiom.brain import transition_stage

logging.basicConfig(level=logging.INFO)

def test_gates():
    init_db()
    strategy_id = str(uuid4())
    
    bad_metrics = {
        "metrics": {
            "total_trades": 10,
            "sharpe": 0.5,
            "max_drawdown_pct": 0.30,
            "profit_factor": 0.8
        }
    }
    
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (id, name, stage, metrics, owner)
            VALUES (?, ?, ?, ?, ?)
            """,
            (strategy_id, "Bad Strategy", "backtesting", json.dumps(bad_metrics), "simulation-agent")
        )
        
    print(f"Inserted strategy {strategy_id}. Attempting to move to paper_trading...")
    
    result = transition_stage(strategy_id, "paper_trading")
    print("Transition result:")
    print(json.dumps(result, indent=2))
    
    assert result["to"] == "rejected", "Strategy should have been rejected by the gate!"
    
    with get_db() as conn:
        row = conn.execute("SELECT stage FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        assert row["stage"] == "rejected", "Database stage should be rejected!"

    print("SUCCESS: Quantitative gates accurately rejected the bad strategy!")

if __name__ == "__main__":
    test_gates()
