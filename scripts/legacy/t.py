import sys  
sys.path.append('C:/Axiom')  
import sqlite3, os  
from axiom.db import get_db  
with get_db() as conn:  
    rows = conn.execute("SELECT id, agent_id, title, status, output_data FROM agent_tasks WHERE agent_id='strategy-developer' AND title LIKE '%%leverage%%' OR title LIKE '%%1.5x%%' ORDER BY id DESC LIMIT 5").fetchall()  
    for r in rows:  
        print('ID:', r[0], 'Agent:', r[1], 'Title:', r[2], 'Status:', r[3])  
        if r[4]: print('Output:', r[4][:500] if len(r[4] or '')  else r[4])  
