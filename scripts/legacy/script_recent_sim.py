import asyncio
import logging
from datetime import datetime, timedelta, timezone
logging.basicConfig(level=logging.INFO)
from axiom.simulation import _runner, start_simulation
from axiom.db import kv_get

async def main():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=10)
    
    start_str = start.strftime("%Y-%m-%dT%H:00:00Z")
    end_str = now.strftime("%Y-%m-%dT%H:00:00Z")
    
    print(f"Running sim from {start_str} to {end_str}")
    await start_simulation(start_str, end_str, '1h', 10000.0, 'direct')
    for t in asyncio.all_tasks():
        if t != asyncio.current_task():
            await t
    print('State:', kv_get('simulation_state'))

asyncio.run(main())