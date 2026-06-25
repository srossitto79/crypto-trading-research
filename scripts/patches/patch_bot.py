import re

with open("Axiom/bot.py", "r") as f:
    content = f.read()

# 1. Add agent_id to __init__
content = re.sub(
    r"    def __init__\(self\):\n        intents = discord\.Intents\.default\(\)",
    "    def __init__(self, agent_id: str | None = None):\n        intents = discord.Intents.default()\n        self.agent_id = agent_id",
    content
)

# 2. Add condition to setup_hook
content = re.sub(
    r"    async def setup_hook\(self\):\n        \"\"\"Called when bot is starting — start background tasks\.\"\"\"\n        self\.scheduler_loop\.start\(\)\n        self\.task_processor_loop\.start\(\)\n        self\.agent_runner_loop\.start\(\)",
    "    async def setup_hook(self):\n        \"\"\"Called when bot is starting — start background tasks.\"\"\"\n        if not self.agent_id:\n            self.scheduler_loop.start()\n            self.task_processor_loop.start()\n            self.agent_runner_loop.start()",
    content
)

# 3. Add condition to on_ready
content = re.sub(
    r"    async def on_ready\(self\):\n        log\.info\(\"Bot connected as %s \(ID: %s\)\", self\.user\.name, self\.user\.id\)",
    "    async def on_ready(self):\n        log.info(\"Bot connected as %s (ID: %s, Agent: %s)\", self.user.name, self.user.id, self.agent_id)\n        if self.agent_id:\n            self._ready_event.set()\n            return",
    content
)

# 4. Modify get_bot
content = re.sub(
    r"def get_bot\(\) -> AxiomBot:\n    \"\"\"Get or create the singleton bot instance\.\"\"\"\n    global _bot\n    if _bot is None:\n        _bot = AxiomBot\(\)\n    return _bot",
    "def get_bot() -> AxiomBot:\n    \"\"\"Get or create the singleton bot instance.\"\"\"\n    global _bot\n    if _bot is None:\n        _bot = AxiomBot(agent_id=None)\n    return _bot",
    content
)

# 5. Modify run_bot and add _run_all_bots
run_all_bots_code = """
async def _run_all_bots():
    from axiom.db import get_db
    token = get_bot_token()
    main_bot = get_bot()
    
    tasks = [main_bot.start(token)]
    
    try:
        with get_db() as conn:
            agents = conn.execute("SELECT id, discord_token FROM agents WHERE enabled=1 AND discord_token IS NOT NULL AND discord_token != ''").fetchall()
        for agent in agents:
            agent_bot = AxiomBot(agent_id=agent["id"])
            tasks.append(agent_bot.start(agent["discord_token"]))
            log.info("Starting Agent Bot for %s", agent["id"])
    except Exception as e:
        log.warning("Could not load agent bots: %s", e)
        
    await asyncio.gather(*tasks)

"""

content = content.replace("def run_bot():", run_all_bots_code + "def run_bot():")

content = re.sub(
    r"    try:\n        token = get_bot_token\(\)\n        bot = get_bot\(\)\n        log\.info\(\"Starting Axiom gateway \(bot \+ scheduler \+ task processor\)\"\)\n        bot\.run\(token\)",
    "    try:\n        log.info(\"Starting Axiom gateway (bot + scheduler + task processor) + agent bots\")\n        asyncio.run(_run_all_bots())",
    content
)

# 6. Modify start_bot
content = re.sub(
    r"        token = get_bot_token\(\)\n        bot = get_bot\(\)\n        await bot\.start\(token\)",
    "        await _run_all_bots()",
    content
)

with open("Axiom/bot.py", "w") as f:
    f.write(content)
print("Patched bot.py")
