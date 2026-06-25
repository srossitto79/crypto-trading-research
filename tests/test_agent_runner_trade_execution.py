from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from axiom.agents import runner
from axiom.db import get_db


def test_run_agent_task_executes_trade_execution_deterministically(AXIOM_db, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks
                (agent_id, type, title, description, status, created_at, input_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "execution-trader",
                "trade_execution",
                "Trade execution open: TR-001 BTC",
                "Structured execution task",
                "pending",
                now,
                json.dumps(
                    {
                        "action": "open",
                        "trade_id": "TR-001",
                        "strategy_id": "S00001",
                        "asset": "BTC",
                        "side": "long",
                        "size": 0.25,
                        "price": 100.0,
                        "stop_loss": 95.0,
                        "source": "scanner.execution_scan",
                    }
                ),
            ),
        )
        task_row = conn.execute(
            "SELECT * FROM agent_tasks WHERE agent_id = 'execution-trader' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        task = dict(task_row)

    executed_payloads: list[dict] = []

    def _fake_execute_trade_intent(intent: dict):
        executed_payloads.append(dict(intent))
        return {"ok": True, "trade_id": intent["trade_id"], "asset": intent["asset"]}

    async def _unexpected_ai_call(*args, **kwargs):
        raise AssertionError("trade_execution tasks should bypass the AI tool loop")

    monkeypatch.setattr("axiom.scanner.execute_trade_intent", _fake_execute_trade_intent)
    monkeypatch.setattr(runner, "_call_with_tools", _unexpected_ai_call)

    output = asyncio.run(
        runner.run_agent_task(
            {"id": "execution-trader", "name": "Execution Trader", "model": "openai", "model_id": "gpt-5.2"},
            task,
        )
    )

    assert output["execution"]["ok"] is True
    assert executed_payloads == [
        {
            "action": "open",
            "trade_id": "TR-001",
            "strategy_id": "S00001",
            "asset": "BTC",
            "side": "long",
            "size": 0.25,
            "price": 100.0,
            "stop_loss": 95.0,
            "source": "scanner.execution_scan",
        }
    ]

    with get_db() as conn:
        row = conn.execute("SELECT status, output_data, error FROM agent_tasks WHERE id = ?", (task["id"],)).fetchone()
        callbacks = conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()

    assert row["status"] == "done"
    payload = json.loads(row["output_data"])
    assert payload["execution"]["trade_id"] == "TR-001"
    assert row["error"] is None
    assert callbacks["count"] == 0
