import re

with open("Axiom/auth/minimax.py", "r") as f:
    content = f.read()

# Fix interval parsing
# From: poll_interval = data.get("interval", 2)
# To: poll_interval = data.get("interval", 2000) / 1000.0 if data.get("interval", 2) > 100 else data.get("interval", 2)

content = content.replace('poll_interval = data.get("interval", 2)  # seconds', 'poll_interval = data.get("interval", 2000) / 1000.0 if data.get("interval", 2) > 100 else data.get("interval", 2)')

content = content.replace('poll_interval_ms = poll_interval * 1000', 'poll_interval_ms = poll_interval * 1000')

with open("Axiom/auth/minimax.py", "w") as f:
    f.write(content)

with open("Axiom/api.py", "r") as f:
    content = f.read()

content = content.replace('interval = int(code_payload.get("interval", 2))', 'interval = int(code_payload.get("interval", 2000)) / 1000.0 if int(code_payload.get("interval", 2)) > 100 else int(code_payload.get("interval", 2))')

with open("Axiom/api.py", "w") as f:
    f.write(content)

