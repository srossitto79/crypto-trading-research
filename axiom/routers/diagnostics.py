"""Diagnostics API: /api/diagnostics/snapshot, /api/diagnostics/resume.

Backs the /diagnostics frontend page (T12) and the LaunchBanner (T15).
The shape of ``snapshot`` is the contract: any change here must be
matched in ``frontend/src/lib/api/diagnostics.ts`` (T11).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from axiom.diagnostics import snapshot
from axiom.task_progress import list_resumable_tasks, resume_task

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/snapshot")
def get_snapshot():
    """Aggregated health checks + cost / truncation rollups."""
    return snapshot()


@router.get("/resumable")
def get_resumable_tasks():
    """List interrupted tasks waiting to be resumed."""
    return {"tasks": list_resumable_tasks()}


@router.post("/resumable/{task_id}/resume")
def post_resume_task(task_id: int):
    """Flip an interrupted task back to ``pending`` so the runner picks it up."""
    if not resume_task(task_id):
        raise HTTPException(status_code=404, detail="task not found or not interrupted")
    return {"ok": True, "task_id": task_id}
