import asyncio

from axiom.control_plane import ops as control_plane_ops
from axiom.control_plane.models import QueueProcessingBody
from axiom.task_timeouts import recommended_stale_recovery_minutes


def test_process_task_queues_defaults_execution_trader_to_fail(monkeypatch):
    captured = {}
    operator_state = {}

    def fake_recover_stale_running_tasks(stale_minutes: int = 10, fail_agents=()):
        captured["stale_minutes"] = int(stale_minutes)
        captured["fail_agents"] = tuple(fail_agents)
        return {"agent_requeued": 0, "agent_failed": 1, "brain_requeued": 0}

    monkeypatch.setattr(control_plane_ops, "kv_get", lambda key, default=None: operator_state if key == "ops_manual_action_state" else default)
    monkeypatch.setattr(control_plane_ops, "kv_set", lambda key, value: operator_state.update(value) if key == "ops_manual_action_state" and isinstance(value, dict) else None)
    monkeypatch.setattr("axiom.db.recover_stale_running_tasks", fake_recover_stale_running_tasks)

    body = QueueProcessingBody(process_agent_tasks=False, process_brain_tasks=False, stale_minutes=7)
    result = asyncio.run(control_plane_ops.process_task_queues(body))

    assert result["ok"] is True
    assert captured["stale_minutes"] == recommended_stale_recovery_minutes({})
    assert "execution-trader" in captured["fail_agents"]
