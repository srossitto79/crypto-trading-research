import sqlite3
import json
from pathlib import Path

def read_funding_tasks():
    db_path = Path.home() / ".Axiom" / "axiom.db"
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, agent_id, title, description, status, input_data, output_data
            FROM agent_tasks 
            WHERE title LIKE '%funding%' OR title LIKE '%reversion%'
            ORDER BY created_at DESC 
            LIMIT 10
        """).fetchall()
        
        for r in rows:
            print("=== Task ID:", r['id'], "===")
            print("Agent:", r['agent_id'])
            print("Title:", r['title'])
            print("Status:", r['status'])
            print("Description:", r['description'])
            print("Input Data:", r['input_data'])
            print("Output Data:", r['output_data'])
            print("-" * 40)
    finally:
        conn.close()

if __name__ == "__main__":
    read_funding_tasks()
import sqlite3
import json
from pathlib import Path

def read_funding_tasks():
    db_path = Path.home() / ".Axiom" / "axiom.db"
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, agent_id, title, description, status, input_data, output_data
            FROM agent_tasks 
            WHERE title LIKE '%funding%' OR title LIKE '%reversion%'
            ORDER BY created_at DESC 
            LIMIT 10
        """).fetchall()
        
        for r in rows:
            print("=== Task ID:", r['id'], "===")
            print("Agent:", r['agent_id'])
            print("Title:", r['title'])
            print("Status:", r['status'])
            print("Description:", r['description'])
            print("Input Data:", r['input_data'])
            print("Output Data:", r['output_data'])
            print("-" * 40)
    finally:
        conn.close()

if __name__ == "__main__":
    read_funding_tasks()
