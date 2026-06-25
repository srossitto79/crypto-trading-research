from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from axiom.db import _now, _parse_json_value, create_approval, get_db

PROTECTION_UNPROTECTED = "unprotected"
PROTECTION_PROTECTED = "protected"
PROTECTION_CONTESTED = "contested"
PROTECTED_STATES = {PROTECTION_PROTECTED, PROTECTION_CONTESTED}

_CRUCIBLE_STATUS_BY_HYPOTHESIS_STATUS = {
    "proposed": "proposed",
    "researching": "testing",
    "proven": "viable",
    "disproven": "failed",
}

# A viable crucible becomes "expanded" once it has grown into a family of
# strategy work — several candidates or a promoted (paper/live) descendant.
EXPANDED_MIN_STRATEGIES = 3
# Strategy stages that count as a promoted descendant (past the gauntlet).
_PROMOTED_DESCENDANT_STAGES = {"paper", "paper_trading", "live_graduated", "deployed"}


def _crucible_status(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    return _CRUCIBLE_STATUS_BY_HYPOTHESIS_STATUS.get(normalized, normalized or "proposed")


def derive_crucible_status(
    *,
    status: str | None,
    strategy_count: int = 0,
    has_promoted_descendant: bool = False,
) -> str:
    """User-facing lifecycle stage in the design-spec vocabulary.

    ``proposed -> testing -> viable -> expanded`` (plus ``failed``). This is the
    label the operator sees. The stored proof ``status``
    (proposed/researching/proven/disproven) stays the source of truth — the
    verdict/promotion/graduation pipeline still keys off it — so this is a
    derived view layered on top, not a parallel stored enum.

    ``expanded`` is a viable (proven + protected) crucible that has spawned a
    family of strategy work: at least :data:`EXPANDED_MIN_STRATEGIES`
    candidates, or any promoted (paper/live) descendant. Without this rule the
    spec's ``expanded`` stage was unreachable.
    """
    base = _crucible_status(status)
    if base == "viable" and (
        has_promoted_descendant or int(strategy_count or 0) >= EXPANDED_MIN_STRATEGIES
    ):
        return "expanded"
    return base


# External source connectors -> ideas HARVESTED from somewhere a human published them.
_HARVESTED_SOURCE_TYPES = {
    "youtube", "reddit", "forum", "blog", "github", "podcast",
    "book", "paper", "web", "url", "article",
}


def derive_origin(source_type: str | None) -> str:
    """Where the idea came from: 'agent' | 'harvested' | 'operator'.

    This is the dimension that actually matters to the operator (it replaces the
    opaque `lane`): did a connected LLM invent this thesis, was it harvested from
    an external source (YouTube/Reddit/forum/podcast/...), or did the operator
    seed it? Agent-invented is the residual/default — it's the dominant path for
    autonomously proposed crucibles.
    """
    st = str(source_type or "").strip().lower()
    if st.startswith("operator"):
        return "operator"
    if st in _HARVESTED_SOURCE_TYPES:
        return "harvested"
    return "agent"


def _row_to_crucible(row: Any) -> dict[str, Any]:
    crucible = dict(row)
    crucible["crucible_id"] = crucible["id"]
    crucible["crucible_status"] = _crucible_status(crucible.get("status"))
    crucible["protection_status"] = (
        str(crucible.get("protection_status") or PROTECTION_UNPROTECTED).strip()
        or PROTECTION_UNPROTECTED
    )
    crucible["target_assets"] = _parse_json_value(crucible.get("target_assets")) or []
    crucible["target_timeframes"] = _parse_json_value(crucible.get("target_timeframes")) or []
    if "verdict_memo" in crucible:
        crucible["verdict_memo"] = _parse_json_value(crucible.get("verdict_memo"))
    return crucible


def get_crucible(crucible_id: str) -> dict[str, Any] | None:
    normalized = str(crucible_id or "").strip()
    if not normalized:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM hypotheses
            WHERE id = ? OR LOWER(TRIM(COALESCE(display_id, ''))) = LOWER(TRIM(?))
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (normalized, normalized, normalized),
        ).fetchone()
    return _row_to_crucible(row) if row else None


def require_crucible(crucible_id: str) -> dict[str, Any]:
    crucible = get_crucible(crucible_id)
    if crucible is None:
        raise ValueError(f"unknown crucible_id: {crucible_id}")
    return crucible


def is_crucible_protected(crucible: dict[str, Any]) -> bool:
    status = str(crucible.get("protection_status") or "").strip().lower()
    return status in PROTECTED_STATES


def mark_crucible_viable(
    crucible_id: str,
    *,
    evidence_id: str,
    by: str,
    evidence_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_id = str(evidence_id or "").strip()
    if not evidence_id:
        raise ValueError("mark_crucible_viable requires a non-empty evidence_id")
    now_iso = _now()
    memo = {
        "event": "crucible_viable",
        "evidence_id": evidence_id,
        "evidence_packet": evidence_packet or {},
        "protection_status": PROTECTION_PROTECTED,
    }
    memo_payload = json.dumps(memo, separators=(",", ":"))

    with get_db() as conn:
        current = conn.execute(
            """
            SELECT *
            FROM hypotheses
            WHERE id = ? OR LOWER(TRIM(COALESCE(display_id, ''))) = LOWER(TRIM(?))
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (crucible_id, crucible_id, crucible_id),
        ).fetchone()
        if current is None:
            raise ValueError(f"unknown crucible_id: {crucible_id}")
        canonical_id = str(current["id"])
        conn.execute(
            """
            UPDATE hypotheses
            SET status = 'proven',
                protection_status = ?,
                protected_at = COALESCE(protected_at, ?),
                protected_by = COALESCE(protected_by, ?),
                initial_viability_evidence_id = COALESCE(initial_viability_evidence_id, ?),
                verdict_memo = ?,
                verdict_memo_at = ?,
                verdict_memo_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                PROTECTION_PROTECTED,
                now_iso,
                by,
                evidence_id,
                memo_payload,
                now_iso,
                by,
                now_iso,
                canonical_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO hypothesis_verdict_memos (id, hypothesis_id, payload, written_at, written_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"HVM-{uuid4().hex[:12]}", canonical_id, memo_payload, now_iso, by),
        )
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (canonical_id,)).fetchone()

    return _row_to_crucible(row)


def request_dethrone_approval(
    crucible_id: str,
    *,
    actor: str,
    reason: str,
    new_evidence: dict[str, Any] | None = None,
    recommended_action: str = "dethrone/archive",
    requested_status: str = "archived",
    conn: Any | None = None,
) -> int:
    now_iso = _now()
    if conn is not None:
        return _request_dethrone_approval_with_conn(
            conn,
            crucible_id,
            actor=actor,
            reason=reason,
            new_evidence=new_evidence,
            recommended_action=recommended_action,
            requested_status=requested_status,
            now_iso=now_iso,
        )
    with get_db() as managed_conn:
        return _request_dethrone_approval_with_conn(
            managed_conn,
            crucible_id,
            actor=actor,
            reason=reason,
            new_evidence=new_evidence,
            recommended_action=recommended_action,
            requested_status=requested_status,
            now_iso=now_iso,
        )


def _request_dethrone_approval_with_conn(
    conn: Any,
    crucible_id: str,
    *,
    actor: str,
    reason: str,
    new_evidence: dict[str, Any] | None,
    recommended_action: str,
    requested_status: str,
    now_iso: str,
) -> int:
    current = conn.execute(
        """
        SELECT *
        FROM hypotheses
        WHERE id = ? OR LOWER(TRIM(COALESCE(display_id, ''))) = LOWER(TRIM(?))
        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (crucible_id, crucible_id, crucible_id),
    ).fetchone()
    if current is None:
        raise ValueError(f"unknown crucible_id: {crucible_id}")
    crucible = _row_to_crucible(current)
    if crucible["crucible_status"] != "viable" or not is_crucible_protected(crucible):
        raise ValueError("dethrone approval requires a viable protected crucible")
    approval_payload = {
        "current_protection_status": crucible["protection_status"],
        "current_crucible_status": crucible["crucible_status"],
        "initial_viability_evidence_id": crucible.get("initial_viability_evidence_id"),
        "current_verdict_memo": crucible.get("verdict_memo"),
        "new_evidence": new_evidence or {},
        "recommended_action": recommended_action,
    }
    existing_approval = conn.execute(
        """
        SELECT id, payload
        FROM approvals
        WHERE approval_type = 'crucible_dethrone'
          AND target_type = 'crucible'
          AND target_id = ?
          AND status IN ('pending', 'pending_approval')
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        (str(crucible["id"]),),
    ).fetchone()
    if existing_approval is not None:
        previous_payload = _parse_json_value(existing_approval["payload"]) or {}
        if previous_payload:
            approval_payload["intent_history"] = [
                *previous_payload.get("intent_history", []),
                {
                    "requested_status": previous_payload.get("new_evidence", {}).get(
                        "requested_manager_state"
                    ),
                    "recommended_action": previous_payload.get("recommended_action"),
                    "new_evidence": previous_payload.get("new_evidence", {}),
                },
            ]
        conn.execute(
            """
            UPDATE approvals
            SET requested_status = ?,
                reason = ?,
                payload = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                requested_status,
                reason,
                json.dumps(approval_payload, separators=(",", ":")),
                now_iso,
                int(existing_approval["id"]),
            ),
        )
        return int(existing_approval["id"])
    approval_id = create_approval(
        "crucible_dethrone",
        target_type="crucible",
        target_id=str(crucible["id"]),
        requested_status=requested_status,
        actor=actor,
        reason=reason,
        owner="ceo",
        payload=approval_payload,
        conn=conn,
    )
    conn.execute(
        """
        UPDATE hypotheses
        SET protection_status = ?,
            contested_at = COALESCE(contested_at, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (PROTECTION_CONTESTED, now_iso, now_iso, str(crucible["id"])),
    )
    return approval_id


def apply_dethrone_decision(
    crucible_id: str,
    *,
    approved: bool,
    requested_manager_state: str = "archived",
) -> dict[str, Any]:
    """Resolve the operator's decision on a contested crucible's dethrone approval.

    When an agent tries to archive a protected crucible, it is flipped to
    ``protection_status = 'contested'`` and a ``crucible_dethrone`` approval is
    queued (see :func:`request_dethrone_approval`). This applies the decision on
    that approval:

    - ``approved=True``  -> clear protection and move the crucible to the
      requested manager_state (``archived``/``trash``). Protection is cleared in
      the SAME transaction first; otherwise the guard in
      ``hypotheses._apply_hypothesis_manager_state`` would re-block the move and
      queue another approval (an infinite trap).
    - ``approved=False`` -> restore ``protection_status = 'protected'`` and clear
      ``contested_at``; the crucible stays a durable, protected asset.

    Before this existed both decisions were silent no-ops, so any contested
    crucible was a permanent one-way trap.
    """
    target_state = str(requested_manager_state or "archived").strip().lower()
    if target_state not in {"archived", "trash"}:
        target_state = "archived"
    now_iso = _now()

    with get_db() as conn:
        current = conn.execute(
            """
            SELECT *
            FROM hypotheses
            WHERE id = ? OR LOWER(TRIM(COALESCE(display_id, ''))) = LOWER(TRIM(?))
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (crucible_id, crucible_id, crucible_id),
        ).fetchone()
        if current is None:
            raise ValueError(f"unknown crucible_id: {crucible_id}")
        canonical_id = str(current["id"])

        if not approved:
            conn.execute(
                """
                UPDATE hypotheses
                SET protection_status = ?,
                    contested_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (PROTECTION_PROTECTED, now_iso, canonical_id),
            )
            row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (canonical_id,)).fetchone()
            result = _row_to_crucible(row)
            result["dethroned"] = False
            return result

        # Approved: clear protection FIRST so the manager-state guard allows the move.
        conn.execute(
            """
            UPDATE hypotheses
            SET protection_status = ?,
                contested_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (PROTECTION_UNPROTECTED, now_iso, canonical_id),
        )
        # Lazy import avoids the hypotheses<->crucibles import cycle.
        from axiom.hypotheses import _apply_hypothesis_manager_state

        _apply_hypothesis_manager_state(conn, canonical_id, target_state, reason="dethrone_approved")
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (canonical_id,)).fetchone()

    result = _row_to_crucible(row)
    result["dethroned"] = True
    return result
