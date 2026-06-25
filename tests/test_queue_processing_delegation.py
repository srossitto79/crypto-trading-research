import asyncio

import pytest

from axiom.control_plane import ops
from axiom.control_plane.models import QueueProcessingBody
from axiom.control_plane.queue_processing import (
    QUEUE_PROCESS_REQUEST_KEY,
    QUEUE_PROCESS_RESULT_KEY,
    build_queue_process_request,
)


def test_process_task_queues_delegates_processing_to_live_bot_worker(monkeypatch):
    store: dict[str, object] = {}

    def fake_kv_get(key: str, default=None):
        if key == QUEUE_PROCESS_RESULT_KEY:
            request = store.get(QUEUE_PROCESS_REQUEST_KEY)
            if isinstance(request, dict) and request.get("request_id"):
                return {
                    "request_id": request["request_id"],
                    "status": "completed",
                    "agent_tasks_processed": True,
                    "brain_tasks_processed": True,
                }
        return store.get(key, default)

    def fake_kv_set(key: str, value):
        store[key] = value

    monkeypatch.setattr(ops, "kv_get", fake_kv_get)
    monkeypatch.setattr(ops, "kv_set", fake_kv_set)
    monkeypatch.setattr(
        ops,
        "_get_bot_lock_status",
        lambda: {
            "lock_held": True,
            "active_pid": 321,
            "active_pid_running": True,
            "task_worker": {"fresh": True},
        },
    )
    monkeypatch.setattr(
        "axiom.db.recover_stale_running_tasks",
        lambda stale_minutes=10, fail_agents=(): {
            "agent_requeued": 1,
            "agent_failed": 0,
            "brain_requeued": 2,
        },
    )

    body = QueueProcessingBody(
        process_agent_tasks=True,
        process_brain_tasks=True,
        recover_stale=True,
        stale_minutes=7,
    )
    result = asyncio.run(ops.process_task_queues(body))

    assert result["ok"] is True
    assert result["delegated_to_bot"] is True
    assert result["queue_request_status"] == "completed"
    assert result["agent_tasks_processed"] is True
    assert result["brain_tasks_processed"] is True
    assert result["recovered"] == {"agent_requeued": 1, "agent_failed": 0, "brain_requeued": 2}
    assert isinstance(store.get(QUEUE_PROCESS_REQUEST_KEY), dict)
    assert store[QUEUE_PROCESS_REQUEST_KEY]["process_agent_tasks"] is True
    assert store[QUEUE_PROCESS_REQUEST_KEY]["process_brain_tasks"] is True


def test_process_task_queues_falls_back_to_local_runtime_when_bot_worker_is_down(monkeypatch):
    store: dict[str, object] = {}

    monkeypatch.setattr(ops, "kv_get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(ops, "kv_set", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(
        ops,
        "_get_bot_lock_status",
        lambda: {"lock_held": False, "active_pid": None, "active_pid_running": False},
    )
    monkeypatch.setattr(
        "axiom.db.recover_stale_running_tasks",
        lambda stale_minutes=10, fail_agents=(): {
            "agent_requeued": 0,
            "agent_failed": 0,
            "brain_requeued": 0,
        },
    )
    monkeypatch.setattr(
        "axiom.runtime_worker.process_agent_tasks_once",
        lambda: asyncio.sleep(0, result=1),
    )

    body = QueueProcessingBody(
        process_agent_tasks=True,
        process_brain_tasks=False,
        recover_stale=False,
    )
    result = asyncio.run(ops.process_task_queues(body))

    assert result["ok"] is True
    assert result["processing_requested"] is True
    assert result["delegated_to_bot"] is False
    assert result["processed_locally"] is True
    assert result["agent_tasks_processed"] is True
    assert result["brain_tasks_processed"] is False
    assert "fell back" in str(result["bot_error"]).lower()
    assert QUEUE_PROCESS_REQUEST_KEY not in store


def test_process_task_queues_falls_back_to_local_runtime_when_bot_lock_is_stale(monkeypatch):
    store: dict[str, object] = {}

    monkeypatch.setattr(ops, "kv_get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(ops, "kv_set", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(
        ops,
        "_get_bot_lock_status",
        lambda: {
            "lock_held": True,
            "active_pid": None,
            "active_pid_running": False,
            "other_process_active": True,
        },
    )
    monkeypatch.setattr(
        "axiom.db.recover_stale_running_tasks",
        lambda stale_minutes=10, fail_agents=(): {
            "agent_requeued": 0,
            "agent_failed": 0,
            "brain_requeued": 0,
        },
    )
    monkeypatch.setattr(
        "axiom.runtime_worker.process_agent_tasks_once",
        lambda: asyncio.sleep(0, result=2),
    )
    monkeypatch.setattr(
        "axiom.runtime_worker.process_brain_tasks_once",
        lambda: asyncio.sleep(0, result=1),
    )

    body = QueueProcessingBody(
        process_agent_tasks=True,
        process_brain_tasks=True,
        recover_stale=False,
    )
    result = asyncio.run(ops.process_task_queues(body))

    assert result["ok"] is True
    assert result["bot_available"] is True
    assert result["delegated_to_bot"] is False
    assert result["processed_locally"] is True
    assert result["agent_tasks_processed"] is True
    assert result["brain_tasks_processed"] is True


def test_bot_consumes_queued_processing_request(monkeypatch):
    pytest.importorskip("discord")
    from axiom.bot import AxiomBot

    bot = AxiomBot(agent_id=None)
    store = {
        QUEUE_PROCESS_REQUEST_KEY: build_queue_process_request(
            process_agent_tasks=True,
            process_brain_tasks=True,
        )
    }
    calls: list[str] = []

    async def fake_process_agent_tasks():
        calls.append("agent")

    async def fake_process_pending_tasks():
        calls.append("brain")

    monkeypatch.setattr("axiom.bot.kv_get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr("axiom.bot.kv_set", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(bot, "_process_agent_tasks", fake_process_agent_tasks)
    monkeypatch.setattr(bot, "_process_pending_tasks", fake_process_pending_tasks)

    asyncio.run(bot._consume_queue_processing_request())

    assert calls == ["agent", "brain"]
    assert store[QUEUE_PROCESS_RESULT_KEY]["status"] == "completed"
    assert store[QUEUE_PROCESS_RESULT_KEY]["agent_tasks_processed"] is True
    assert store[QUEUE_PROCESS_RESULT_KEY]["brain_tasks_processed"] is True
