import sys
sys.path.append('C:/Axiom')
from axiom.db import get_db, kv_get, AXIOM_DB as DB_PATH

print('DB Path:', DB_PATH)
print('Global Mode:', kv_get('Axiom:settings', {}).get('execution_mode'))
print('Has Hyperliquid Key:', 'hyperliquid_private_key' in kv_get('Axiom:secrets', {}))

with get_db() as conn:
    trades = conn.execute('SELECT execution_type, status, count(*) as count FROM trades GROUP BY execution_type, status').fetchall()
    print('Trades by Type and Status:', [dict(t) for t in trades])
    
    strategies = conn.execute('SELECT status, count(*) as count FROM strategies GROUP BY status').fetchall()
    print('Strategies by Status:', [dict(s) for s in strategies])
