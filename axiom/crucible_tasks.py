"""Planner task validation for crucible-originated strategy candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from axiom.db import get_db

CANDIDATE_ACTION_KINDS = {"develop_candidate", "expand_viable_crucible"}
TRUSTED_CANDIDATE_ORIGINS = {
    "autonomous_follow_through",
    "brain_assigned",
    "crucible_planner",
    "hypothesis_promotion_loop",
    "operator_generate_strategies",
    "operator_manual_entry",
    "operator_url_paste",
}


@dataclass(frozen=True)
class CandidateStrategyCreationValidation:
    allowed: bool
    reason: str = ""
    crucible_id: str | None = None
    hypothesis_id: str | None = None


def _parse_json_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _task_numeric_id(task_display_id: str) -> int | None:
    normalized = str(task_display_id or "").strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    suffix = normalized[1:] if normalized[:1].upper() == "T" else ""
    return int(suffix) if suffix.isdigit() else None


def get_agent_task_payload(task_display_id: str) -> dict[str, Any]:
    """Return a running agent task's input_data payload by display id."""
    task = _get_agent_task(task_display_id)
    if str(task.get("status") or "").strip() != "running":
        return {}
    return _parse_json_object(task.get("input_data"))


def _get_agent_task(task_display_id: str) -> dict[str, Any]:
    normalized_display_id = str(task_display_id or "").strip()
    numeric_id = _task_numeric_id(normalized_display_id)
    with get_db() as conn:
        if numeric_id is not None:
            row = conn.execute(
                """
                SELECT agent_id, status, input_data
                FROM agent_tasks
                WHERE display_id = ? OR id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_display_id, numeric_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT agent_id, status, input_data
                FROM agent_tasks
                WHERE display_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_display_id,),
            ).fetchone()
    if not row:
        return {}
    return dict(row)


def _task_payload_matches_candidate_request(
    payload: dict[str, Any],
    normalized_crucible_id: str,
    normalized_hypothesis_id: str,
) -> bool:
    origin_mode = str(payload.get("origin_mode") or "").strip()
    if origin_mode not in TRUSTED_CANDIDATE_ORIGINS:
        return False
    action_kind = str(payload.get("action_kind") or "").strip()
    if origin_mode == "crucible_planner" and action_kind not in CANDIDATE_ACTION_KINDS:
        return False
    if origin_mode != "crucible_planner" and action_kind and action_kind not in CANDIDATE_ACTION_KINDS:
        return False

    # Brain-dispatched tasks (origin_mode="brain_assigned") are created without
    # a specific crucible_id because the brain doesn't know at dispatch time which
    # crucible the agent will target. The brain is already trusted at the top of
    # validate_candidate_strategy_creation; its dispatched tasks inherit that trust.
    if origin_mode == "brain_assigned":
        return True

    payload_crucible_id = str(payload.get("crucible_id") or "").strip()
    payload_hypothesis_id = str(payload.get("hypothesis_id") or "").strip()
    if not payload_crucible_id and not payload_hypothesis_id:
        return False
    payload_ids = {payload_id for payload_id in (payload_crucible_id, payload_hypothesis_id) if payload_id}
    if (
        normalized_hypothesis_id
        and normalized_hypothesis_id != normalized_crucible_id
        and not (
            payload_crucible_id
            and payload_hypothesis_id
            and normalized_crucible_id == payload_crucible_id
            and normalized_hypothesis_id == payload_hypothesis_id
        )
    ):
        return False
    return normalized_crucible_id in payload_ids


def _find_matching_running_candidate_task(
    normalized_agent_id: str,
    normalized_crucible_id: str,
    normalized_hypothesis_id: str,
) -> dict[str, Any]:
    if not normalized_agent_id or not normalized_crucible_id:
        return {}
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT agent_id, status, input_data
            FROM agent_tasks
            WHERE agent_id = ?
              AND status = 'running'
            ORDER BY COALESCE(started_at, created_at) DESC
            LIMIT 20
            """,
            (normalized_agent_id,),
        ).fetchall()
    for row in rows:
        task = dict(row)
        payload = _parse_json_object(task.get("input_data"))
        if _task_payload_matches_candidate_request(
            payload,
            normalized_crucible_id,
            normalized_hypothesis_id,
        ):
            return task
    return {}


def validate_candidate_strategy_creation(
    crucible_id: str | None,
    agent_id: str | None,
    task_display_id: str | None,
    hypothesis_id: str | None = None,
) -> CandidateStrategyCreationValidation:
    """Allow manual calls, but require agent-created candidates to come from trusted work."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == "brain":
        return CandidateStrategyCreationValidation(
            True,
            crucible_id=str(crucible_id or "").strip() or None,
            hypothesis_id=str(hypothesis_id or crucible_id or "").strip() or None,
        )

    normalized_crucible_id = str(crucible_id or "").strip()
    normalized_hypothesis_id = str(hypothesis_id or normalized_crucible_id).strip()
    if not normalized_crucible_id:
        return CandidateStrategyCreationValidation(
            False,
            "Agent-created strategy candidates require a planner-approved crucible_id.",
        )

    task = _get_agent_task(str(task_display_id or "").strip())
    task_agent_id = str(task.get("agent_id") or "").strip()
    task_status = str(task.get("status") or "").strip()
    if task_status != "running":
        task = _find_matching_running_candidate_task(
            normalized_agent_id,
            normalized_crucible_id,
            normalized_hypothesis_id,
        )
        task_agent_id = str(task.get("agent_id") or "").strip()
        task_status = str(task.get("status") or "").strip()
        if task_status != "running":
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates require a planner-approved running crucible task.",
            )
    if task_agent_id != normalized_agent_id:
        fallback_task = _find_matching_running_candidate_task(
            normalized_agent_id,
            normalized_crucible_id,
            normalized_hypothesis_id,
        )
        if fallback_task:
            task = fallback_task
            task_agent_id = str(task.get("agent_id") or "").strip()
        if task_agent_id != normalized_agent_id:
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates require a planner-approved task assigned to the current agent.",
            )

    payload = _parse_json_object(task.get("input_data"))
    if not payload:
        return CandidateStrategyCreationValidation(
            False,
            "Agent-created strategy candidates require a planner-approved running crucible task.",
        )
    if not _task_payload_matches_candidate_request(
        payload,
        normalized_crucible_id,
        normalized_hypothesis_id,
    ):
        origin_mode = str(payload.get("origin_mode") or "").strip()
        action_kind = str(payload.get("action_kind") or "").strip()
        payload_crucible_id = str(payload.get("crucible_id") or "").strip()
        payload_hypothesis_id = str(payload.get("hypothesis_id") or "").strip()
        if origin_mode not in TRUSTED_CANDIDATE_ORIGINS:
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates require a trusted crucible candidate task.",
            )
        if origin_mode == "crucible_planner" and action_kind not in CANDIDATE_ACTION_KINDS:
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates require a planner-approved candidate task.",
            )
        if origin_mode != "crucible_planner" and action_kind and action_kind not in CANDIDATE_ACTION_KINDS:
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates require a trusted candidate task kind.",
            )
        if not payload_crucible_id and not payload_hypothesis_id:
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates require a planner-approved crucible task match.",
            )
        if (
            normalized_hypothesis_id
            and normalized_hypothesis_id != normalized_crucible_id
            and not (
                payload_crucible_id
                and payload_hypothesis_id
                and normalized_crucible_id == payload_crucible_id
                and normalized_hypothesis_id == payload_hypothesis_id
            )
        ):
            return CandidateStrategyCreationValidation(
                False,
                "Agent-created strategy candidates must use the planner-approved crucible_id and hypothesis_id pair.",
            )
        return CandidateStrategyCreationValidation(
            False,
            "Agent-created strategy candidates require a planner-approved matching crucible task.",
        )

    payload_crucible_id = str(payload.get("crucible_id") or "").strip()
    payload_hypothesis_id = str(payload.get("hypothesis_id") or "").strip()

    canonical_hypothesis_id = payload_hypothesis_id or payload_crucible_id or normalized_crucible_id
    canonical_crucible_id = payload_crucible_id or canonical_hypothesis_id
    return CandidateStrategyCreationValidation(
        True,
        crucible_id=canonical_crucible_id,
        hypothesis_id=canonical_hypothesis_id,
    )
