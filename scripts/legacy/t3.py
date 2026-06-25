import sqlite3, os  
h = os.path.expanduser("~")  
db = os.path.join(h, ".Axiom", "axiom.db")  
conn = sqlite3.connect(db)  
rows = conn.execute("SELECT id, name, params FROM strategies WHERE status='paper'").fetchall()  
for r in rows: print(r)  
import sqlite3, os  
h = os.path.expanduser("~")  
db = os.path.join(h, ".Axiom", "axiom.db")  
conn = sqlite3.connect(db)  
cols = conn.execute("PRAGMA table_info(strategies)").fetchall()  
print([c[1] for c in cols])  
