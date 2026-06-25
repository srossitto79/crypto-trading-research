from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from axiom.api_security import require_operator_access
from axiom.control_plane import approvals as control_plane_approvals
from axiom.control_plane import approval_modes as control_plane_approval_modes
from axiom.control_plane.models import ApprovalDecisionBody, ApprovalHandoffBody, ApprovalTroubleshootBody
from axiom.control_plane.smart_approval import classify_approval

router = APIRouter(tags=["approvals"], dependencies=[Depends(require_operator_access)])


class ApprovalModesBody(BaseModel):
    modes: dict[str, str] = {}
    default_mode: str = "manual"
    deadlines_hours: dict[str, int] = {}
    default_deadline_hours: int = 72
    escalation_owner: str = ""


class BulkApproveBody(BaseModel):
    approval_ids: list[int]
    actor: str | None = None
    feedback: str | None = None


@router.get("/api/approvals")
def get_approvals(
    status: str | None = None,
    approval_type: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    owner: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    return control_plane_approvals.get_approvals_list(
        status=status,
        approval_type=approval_type,
        target_type=target_type,
        target_id=target_id,
        owner=owner,
        limit=limit,
        offset=offset,
    )


@router.post("/api/approvals/{approval_id}/approve")
def approve_approval(approval_id: int, body: ApprovalDecisionBody):
    return control_plane_approvals.post_approve_approval(approval_id, body)


@router.post("/api/approvals/{approval_id}/deny")
def deny_approval(approval_id: int, body: ApprovalDecisionBody):
    return control_plane_approvals.post_deny_approval(approval_id, body)


@router.post("/api/approvals/{approval_id}/revise")
def revise_approval(approval_id: int, body: ApprovalDecisionBody):
    return control_plane_approvals.post_revise_approval(approval_id, body)


@router.post("/api/approvals/{approval_id}/handoff")
def handoff_approval(approval_id: int, body: ApprovalHandoffBody):
    return control_plane_approvals.post_handoff_approval(approval_id, body)


@router.get("/api/approvals/{approval_id}/context")
def get_approval_context(approval_id: int):
    return control_plane_approvals.get_approval_context(approval_id)


@router.post("/api/approvals/{approval_id}/user-complete")
def user_complete_approval(approval_id: int, body: ApprovalDecisionBody):
    return control_plane_approvals.post_user_complete_approval(approval_id, body)


@router.post("/api/approvals/{approval_id}/troubleshoot")
def troubleshoot_approval(approval_id: int, body: ApprovalTroubleshootBody | None = None):
    return control_plane_approvals.post_troubleshoot_approval(approval_id, body)


# Phase 5 / P5-T07: smart-approval / modes / bulk endpoints.

@router.post("/api/approvals/{approval_id}/classify")
def classify_approval_route(approval_id: int) -> dict[str, Any]:
    """Re-run the smart-approval classifier on demand (operator UI button)."""
    from axiom.db import get_approval as db_get_approval
    approval = db_get_approval(int(approval_id))
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return classify_approval(approval)


@router.get("/api/approvals/modes")
def get_approval_modes() -> dict[str, Any]:
    settings = control_plane_approval_modes.get_settings()
    return {
        **settings,
        "valid_modes": list(control_plane_approval_modes.VALID_MODES),
        "off_allowlist": sorted(control_plane_approval_modes.OFF_ALLOWLIST),
        "known_categories": control_plane_approval_modes.list_known_categories(),
    }


@router.put("/api/approvals/modes")
def put_approval_modes(body: ApprovalModesBody) -> dict[str, Any]:
    saved = control_plane_approval_modes.save_settings(body.model_dump())
    return saved


@router.post("/api/approvals/bulk-approve")
def bulk_approve(body: BulkApproveBody) -> dict[str, Any]:
    """Approve only IDs whose ``classifier_recommendation == 'auto_approve'``.

    Server-side filter is the gate — the frontend cannot bypass this.
    """
    from axiom.db import get_db

    if not body.approval_ids:
        return {"approved": [], "skipped": [], "missing": []}

    approved: list[int] = []
    skipped: list[int] = []
    missing: list[int] = []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, classifier_recommendation, status FROM approvals "
            f"WHERE id IN ({','.join('?' for _ in body.approval_ids)})",
            tuple(int(x) for x in body.approval_ids),
        ).fetchall()
    found = {int(r["id"]): r for r in rows}
    for raw_id in body.approval_ids:
        aid = int(raw_id)
        row = found.get(aid)
        if row is None:
            missing.append(aid)
            continue
        if (
            (row["classifier_recommendation"] or "").strip() == "auto_approve"
            and (row["status"] or "").strip() == "pending_approval"
        ):
            try:
                control_plane_approvals.post_approve_approval(
                    aid,
                    ApprovalDecisionBody(
                        actor=body.actor or "system:bulk_approve",
                        feedback=body.feedback or "bulk-approved (classifier=auto_approve)",
                    ),
                )
                with get_db() as c2:
                    c2.execute(
                        "UPDATE approvals SET auto_approved = 1 WHERE id = ?",
                        (aid,),
                    )
                approved.append(aid)
            except Exception:
                skipped.append(aid)
        else:
            skipped.append(aid)
    return {"approved": approved, "skipped": skipped, "missing": missing}
