import sys
sys.path.append('C:/Axiom')
from axiom.db import get_db, kv_get

def check_status():
    with get_db() as conn:
        print("--- Strategies ---")
        strategies = conn.execute("SELECT status, stage, owner, count(*) as count FROM strategies GROUP BY status, stage, owner").fetchall()
        for r in strategies:
            print(dict(r))
            
        print("\n--- Strategy Candidates ---")
        candidates = conn.execute("SELECT count(*) as count FROM strategy_candidates").fetchone()
        print(f"Total candidates: {candidates['count']}")
        
        print("\n--- Approvals ---")
        approvals = conn.execute("SELECT status, count(*) as count FROM approvals GROUP BY status").fetchall()
        for r in approvals:
            print(dict(r))
            
        print("\n--- Running Agent Tasks ---")
        running_tasks = conn.execute("SELECT id, agent_id, type, title, status, started_at FROM agent_tasks WHERE status = 'running'").fetchall()
        for r in running_tasks:
            print(dict(r))

        print("\n--- Settings ---")
        settings = kv_get('Axiom:settings', {})
        print(f"Execution Mode: {settings.get('execution_mode')}")
        print(f"Autopilot: {settings.get('autopilot_enabled')}")

if __name__ == "__main__":
    check_status()
