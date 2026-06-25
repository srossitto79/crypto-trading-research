"""Compatibility wrappers for the dedicated Regime Lab worker service."""

from __future__ import annotations

from typing import Any

from axiom.lab_worker_service import get_lab_worker_status, run_lab_worker_loop


def kick_matrix_worker() -> bool:
    """Compatibility shim.

    Matrix work now runs only in the dedicated lab worker process, so API callers
    should enqueue jobs and let the external worker pick them up.
    """
    return False


def get_matrix_worker_status() -> dict[str, Any]:
    return get_lab_worker_status()


def drain_matrix_queue_once() -> dict[str, Any]:
    return run_lab_worker_loop(once=True)
