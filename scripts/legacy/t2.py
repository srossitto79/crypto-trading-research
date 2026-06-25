from axiom.db import get_db  
r = get_db().__enter__().execute("SELECT output_data FROM agent_tasks WHERE id=577").fetchone()  
print(r[0] if r else "No output")  
