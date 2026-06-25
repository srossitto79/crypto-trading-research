import re

with open("Axiom/reporter.py", "r") as f:
    content = f.read()

content = content.replace(
    "async def _send_embeds(channel_name: str, embeds: list[dict]):",
    "async def _send_embeds(channel_name: str, embeds: list[dict], agent_id: str | None = None):"
)

content = content.replace(
    "await _send_embeds(channel_name, embeds)",
    "await _send_embeds(channel_name, embeds, agent_id)"
)

new_token_logic = """
    token = get_bot_token()
    if agent_id:
        from axiom.db import get_db
        with get_db() as conn:
            agent = conn.execute("SELECT discord_token FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if agent and agent["discord_token"]:
                token = agent["discord_token"]
"""

content = re.sub(
    r"    token = get_bot_token\(\)",
    new_token_logic.strip(),
    content
)

with open("Axiom/reporter.py", "w") as f:
    f.write(content)
print("Patched reporter.py")
