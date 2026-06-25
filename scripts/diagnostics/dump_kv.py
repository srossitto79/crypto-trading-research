import sqlite3
from pathlib import Path
import json

def dump_kv():
    db_path = Path.home() / ".Axiom" / "axiom.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT key, value FROM kv").fetchall()
        for r in rows:
            print(f"Key: {r['key']}")
            try:
                val = json.loads(r['value'])
                print(f"Value: {json.dumps(val, indent=2)}")
            except:
                print(f"Value: {r['value']}")
            print("-" * 20)
    finally:
        conn.close()

if __name__ == "__main__":
    dump_kv()
