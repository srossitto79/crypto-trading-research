"""Dormancy controls for Regime Lab."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import psutil

from axiom.config import AXIOM_HOME
from axiom.lab_db import (
    LabJobState,
    get_lab_meta,
    get_regime_program,
    init_lab_db,
    list_lab_jobs,
    set_lab_job_state,
    set_lab_meta,
    update_regime_program,
)
from axiom.lab_features import regime_lab_enabled
from axiom.lab_orchestrator import (
    DEFAULT_ORCHESTRATOR_CONFIG,
    DEFAULT_ORCHESTRATOR_STATUS,
    ORCHESTRATOR_CONFIG_META_KEY,
    ORCHESTRATOR_STATUS_META_KEY,
)

LAB_WORKER_STATUS_META_KEY = "lab_worker_status"
LAB_WORKER_PID_FILE = "lab_worker.pid"


def regime_lab_dormancy_message(*, action: str | None = None) -> str:
    action_suffix = f" and cannot {action}" if action else ""
    return (
        f"Regime Lab is dormant{action_suffix}. "
        "Set AXIOM_ENABLE_REGIME_LAB=1 to re-enable it."
    )


def _lab_worker_pid_path() -> Path:
    return AXIOM_HOME / "lab" / LAB_WORKER_PID_FILE


def _normalize_orchestrator_config() -> dict[str, Any]:
    raw = get_lab_meta(ORCHESTRATOR_CONFIG_META_KEY, {})
    config = dict(DEFAULT_ORCHESTRATOR_CONFIG)
    if isinstance(raw, dict):
        config.update(raw)
    return config


def _normalize_orchestrator_status() -> dict[str, Any]:
    raw = get_lab_meta(ORCHESTRATOR_STATUS_META_KEY, {})
    status = dict(DEFAULT_ORCHESTRATOR_STATUS)
    if isinstance(raw, dict):
        status.update(raw)
    return status


def _stop_worker_processes(pid_candidates: set[int]) -> int:
    stopped = 0
    for pid in sorted(pid_candidates):
        if pid <= 0:
            continue
        try:
            process = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        try:
            process.terminate()
            process.wait(timeout=3)
            stopped += 1
        except psutil.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
                stopped += 1
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            continue
    return stopped


def quiesce_regime_lab(*, reason: str = "feature_dormant") -> dict[str, int]:
    """Force Regime Lab into a dormant, non-running state without deleting data."""
    init_lab_db()

    config = _normalize_orchestrator_config()
    config["enabled"] = False
    config["auto_start_worker"] = False
    set_lab_meta(ORCHESTRATOR_CONFIG_META_KEY, config)

    status = _normalize_orchestrator_status()
    status["state"] = "paused"
    status["next_run_at"] = None
    status["pending_model_job_id"] = None
    status["pending_segments_job_id"] = None
    status["pending_matrix_job_id"] = None
    status["last_error"] = reason
    set_lab_meta(ORCHESTRATOR_STATUS_META_KEY, status)

    program_id = str(config.get("program_id") or status.get("program_id") or "").strip()
    if program_id:
        program = get_regime_program(program_id)
        if program is not None:
            update_regime_program(program.id, status="paused")

    failed_jobs = 0
    while True:
        jobs = list_lab_jobs(states=[LabJobState.QUEUED, LabJobState.RUNNING], limit=500)
        if not jobs:
            break
        for job in jobs:
            updated = set_lab_job_state(
                job.id,
                state=LabJobState.FAILED,
                error_json={"error": reason, "reason": "Regime Lab is dormant"},
                progress_json={"phase": "dormant"},
            )
            if updated is not None:
                failed_jobs += 1

    worker_meta = get_lab_meta(LAB_WORKER_STATUS_META_KEY, {})
    normalized_worker_meta = dict(worker_meta) if isinstance(worker_meta, dict) else {}

    pid_candidates: set[int] = set()
    for candidate in (
        normalized_worker_meta.get("pid"),
        normalized_worker_meta.get("active_pid"),
    ):
        try:
            pid_candidates.add(int(candidate))
        except (TypeError, ValueError):
            continue

    pid_path = _lab_worker_pid_path()
    if pid_path.exists():
        try:
            pid_candidates.add(int(pid_path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            pass

    stopped_workers = _stop_worker_processes(pid_candidates)

    normalized_worker_meta["state"] = "stopped"
    normalized_worker_meta["pid"] = None
    normalized_worker_meta["current_job_id"] = None
    normalized_worker_meta["last_error"] = reason
    normalized_worker_meta["heartbeat_at"] = time.time()
    normalized_worker_meta["is_stale"] = True
    set_lab_meta(LAB_WORKER_STATUS_META_KEY, normalized_worker_meta)

    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass

    return {
        "failed_jobs": failed_jobs,
        "stopped_workers": stopped_workers,
    }


def ensure_regime_lab_enabled(*, action: str, reason: str = "feature_dormant") -> None:
    if regime_lab_enabled():
        return
    quiesce_regime_lab(reason=reason)
    raise RuntimeError(regime_lab_dormancy_message(action=action))


__all__ = [
    "ensure_regime_lab_enabled",
    "quiesce_regime_lab",
    "regime_lab_dormancy_message",
]
