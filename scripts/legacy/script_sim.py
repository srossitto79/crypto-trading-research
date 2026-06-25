import asyncio
import logging
logging.basicConfig(level=logging.INFO)
from axiom.simulation import _runner, start_simulation
from axiom.db import kv_get

async def main():
    await start_simulation('2023-01-01T00:00:00Z', '2023-01-02T00:00:00Z', '1h', 10000.0, 'direct')
    for t in asyncio.all_tasks():
        if t != asyncio.current_task():
            await t
    print('State:', kv_get('simulation_state'))

asyncio.run(main())