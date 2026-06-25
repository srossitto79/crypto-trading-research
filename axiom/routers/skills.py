"""Phase 3 (P3-T08) — Quant Skills API.

Backs the SvelteKit Memory Skills tab + Skill detail drawer (P3-T11/T12) and
the dashboard "declining skills" widget (P3-T15). Operator-gated via
require_operator_access: the quant-skills KB (what_works/evidence/full
content) is moat IP, so this router must NOT be reachable with only the
api-key — kept consistent with the /api/quant-skills endpoints in
strategies.py rather than relying on the global ApiKeyMiddleware alone.
Read-only — operator edits flow through the `skill_update_proposal`
approval queue, not via direct PUT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from axiom import quant_skills as qs
from axiom import skill_outcomes as so
from axiom.api_security import require_operator_access
from axiom.db import get_db

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=[Depends(require_operator_access)])


@router.get("")
def list_skills() -> dict[str, Any]:
    """L1 disclosure — metadata-only catalog (target ~2k tokens for ~100 skills)."""
    items = qs.quant_skills_list()
    return {"items": items, "count": len(items)}


@router.get("/declining")
def list_declining_skills(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """Skills with negative outcome events in the last N days, ranked by
    cumulative confidence delta (most negative first).

    Backs the dashboard "skills going stale" widget. The query joins
    skill_outcome_events with the catalog so already-archived skills (file
    removed) don't surface stale rows.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT skill_name, "
            "SUM(confidence_delta) AS total_delta, "
            "COUNT(*) AS event_count, "
            "MAX(created_at) AS last_event_at "
            "FROM skill_outcome_events "
            "WHERE created_at >= ? "
            "GROUP BY skill_name "
            "HAVING total_delta < 0 "
            "ORDER BY total_delta ASC LIMIT ?",
            (cutoff, int(limit)),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        skill = qs.read_skill(r["skill_name"])
        if skill is None:
            continue  # archived — drop from widget
        items.append({
            "skill_name": r["skill_name"],
            "total_delta": float(r["total_delta"]),
            "event_count": int(r["event_count"]),
            "last_event_at": r["last_event_at"],
            "confidence": skill.confidence,
            "version": skill.version,
        })

    return {"items": items, "days": int(days), "count": len(items)}


@router.get("/{name}")
def get_skill(name: str) -> dict[str, Any]:
    """L2 disclosure — full skill detail."""
    detail = qs.quant_skill_view(name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
    return detail


@router.get("/{name}/section/{section}")
def get_skill_section(name: str, section: str) -> dict[str, Any]:
    """L3 disclosure — single section of a skill (what_works, evidence, history, ...)."""
    try:
        result = qs.quant_skill_view(name, section=section)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found")
    if isinstance(result, list):
        return {"section": section, "items": result}
    return {"section": section, **result}


@router.get("/{name}/history")
def get_skill_history(name: str) -> dict[str, Any]:
    """All `quant_skills_history` rows for the skill, version DESC."""
    history = qs.list_skill_history(name)
    return {"skill_name": name, "history": history, "count": len(history)}


@router.get("/{name}/diff")
def get_skill_diff_endpoint(
    name: str,
    from_version: int = Query(..., ge=1, description="Lower bound (exclusive)"),
    to_version: int = Query(..., ge=1, description="Upper bound (inclusive)"),
) -> dict[str, Any]:
    """Concatenated unified diff between two skill versions."""
    diff = qs.get_skill_diff(name, from_version=from_version, to_version=to_version)
    return {
        "skill_name": name,
        "from_version": from_version,
        "to_version": to_version,
        "diff": diff,
    }


@router.get("/{name}/outcomes")
def get_skill_outcomes(
    name: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Outcome closure events for the skill, ordered created_at DESC."""
    items = so.list_skill_outcomes(skill_name=name, limit=limit, offset=offset)
    return {"skill_name": name, "items": items, "count": len(items)}


__all__ = ["router"]
