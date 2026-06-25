from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

QUEUE_PROCESS_REQUEST_KEY = "ops:queue_process_request"
QUEUE_PROCESS_RESULT_KEY = "ops:queue_process_result"
QUEUE_PROCESS_ACTIVE_STATUSES = {"queued", "processing"}
QUEUE_PROCESS_STALE_AFTER_SECONDS = 300.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_active_request(payload: object, *, max_age_seconds: float = QUEUE_PROCESS_STALE_AFTER_SECONDS) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status not in QUEUE_PROCESS_ACTIVE_STATUSES:
        return False
    updated_at = parse_timestamp(payload.get("updated_at") or payload.get("requested_at"))
    if updated_at is None:
        return True
    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return age_seconds <= max(float(max_age_seconds), 1.0)


def build_queue_process_request(
    *,
    process_agent_tasks: bool,
    process_brain_tasks: bool,
    source: str = "ops_api",
) -> dict[str, Any]:
    return {
        "request_id": uuid4().hex,
        "status": "queued",
        "source": str(source or "ops_api").strip() or "ops_api",
        "process_agent_tasks": bool(process_agent_tasks),
        "process_brain_tasks": bool(process_brain_tasks),
        "requested_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def build_queue_process_result(
    request_id: str,
    *,
    status: str,
    agent_tasks_processed: bool = False,
    brain_tasks_processed: bool = False,
    error: str | None = None,
    worker_pid: int | None = None,
) -> dict[str, Any]:
    return {
        "request_id": str(request_id or "").strip(),
        "status": str(status or "unknown").strip().lower() or "unknown",
        "agent_tasks_processed": bool(agent_tasks_processed),
        "brain_tasks_processed": bool(brain_tasks_processed),
        "error": str(error).strip() if error else None,
        "worker_pid": int(worker_pid) if isinstance(worker_pid, int) else worker_pid,
        "updated_at": utc_now_iso(),
    }


__all__ = [
    "QUEUE_PROCESS_REQUEST_KEY",
    "QUEUE_PROCESS_RESULT_KEY",
    "QUEUE_PROCESS_STALE_AFTER_SECONDS",
    "build_queue_process_request",
    "build_queue_process_result",
    "is_active_request",
    "parse_timestamp",
    "utc_now_iso",
]
