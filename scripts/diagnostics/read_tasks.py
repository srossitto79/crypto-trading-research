import sys
import os
import sqlite3
import json
from pathlib import Path

def read_tasks():
    db_path = Path.home() / ".Axiom" / "axiom.db"
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, agent_id, title, description, input_data FROM agent_tasks WHERE id IN (481, 482)").fetchall()
        for r in rows:
            print(f"Task ID: {r['id']}")
            print(f"Agent ID: {r['agent_id']}")
            print(f"Title: {r['title']}")
            print(f"Description: {r['description']}")
            print(f"Input Data: {r['input_data']}")
            print("-" * 20)
    finally:
        conn.close()

if __name__ == "__main__":
    read_tasks()
