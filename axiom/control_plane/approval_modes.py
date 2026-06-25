"""Approval-mode settings (Phase 5 / P5-T04).

Operator chooses ``manual|smart|off`` per approval category. Persisted in
the kv-store under ``settings:approval_modes`` with shape:

    {
        "modes": { "<approval_type>": "manual"|"smart"|"off" },
        "default_mode": "manual",
        "deadlines_hours": { "<approval_type>": <int> },
        "default_deadline_hours": 72,
        "escalation_owner": "<email or label>"
    }

``smart`` mode runs the smart-approval classifier and auto-approves rows the
classifier returns ``auto_approve`` for; ``off`` mode auto-approves with no
classifier (gated server-side to a small allowlist of safe categories — never
high-stakes).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from axiom.db import get_db, kv_get, kv_set

log = logging.getLogger("axiom.control_plane.approval_modes")

SETTINGS_KEY = "settings:approval_modes"

VALID_MODES = ("manual", "smart", "off")

# Categories where mode='off' (auto-approve without classifier) is permitted.
# Anything else with mode='off' falls back to 'manual' on apply — defense in
# depth so an operator can't accidentally skip review of a strategy deploy.
OFF_ALLOWLIST = frozenset({
    "param_optimization",
    "data_gap_followup",
})

DEFAULT_DEADLINE_HOURS = 72

DEFAULT_SETTINGS: dict[str, Any] = {
    "modes": {},
    "default_mode": "manual",
    "deadlines_hours": {},
    "default_deadline_hours": DEFAULT_DEADLINE_HOURS,
    "escalation_owner": "",
}


def _coerce_mode(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in VALID_MODES else "manual"


def get_settings() -> dict[str, Any]:
    raw = kv_get(SETTINGS_KEY)
    if not raw:
        return dict(DEFAULT_SETTINGS)
    try:
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return dict(DEFAULT_SETTINGS)
    except Exception:
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(data.get("modes"), dict):
        merged["modes"] = {
            str(k).strip().lower(): _coerce_mode(v)
            for k, v in data["modes"].items()
            if str(k).strip()
        }
    merged["default_mode"] = _coerce_mode(data.get("default_mode") or "manual")
    if isinstance(data.get("deadlines_hours"), dict):
        deadlines: dict[str, int] = {}
        for k, v in data["deadlines_hours"].items():
            try:
                deadlines[str(k).strip().lower()] = max(1, int(v))
            except Exception:
                continue
        merged["deadlines_hours"] = deadlines
    try:
        merged["default_deadline_hours"] = max(
            1, int(data.get("default_deadline_hours") or DEFAULT_DEADLINE_HOURS)
        )
    except Exception:
        merged["default_deadline_hours"] = DEFAULT_DEADLINE_HOURS
    merged["escalation_owner"] = str(data.get("escalation_owner") or "").strip()
    return merged


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(DEFAULT_SETTINGS)
    if isinstance(payload.get("modes"), dict):
        coerced["modes"] = {
            str(k).strip().lower(): _coerce_mode(v)
            for k, v in payload["modes"].items()
            if str(k).strip()
        }
    coerced["default_mode"] = _coerce_mode(payload.get("default_mode") or "manual")
    if isinstance(payload.get("deadlines_hours"), dict):
        deadlines: dict[str, int] = {}
        for k, v in payload["deadlines_hours"].items():
            try:
                deadlines[str(k).strip().lower()] = max(1, int(v))
            except Exception:
                continue
        coerced["deadlines_hours"] = deadlines
    try:
        coerced["default_deadline_hours"] = max(
            1, int(payload.get("default_deadline_hours") or DEFAULT_DEADLINE_HOURS)
        )
    except Exception:
        coerced["default_deadline_hours"] = DEFAULT_DEADLINE_HOURS
    coerced["escalation_owner"] = str(payload.get("escalation_owner") or "").strip()
    kv_set(SETTINGS_KEY, json.dumps(coerced))
    return coerced


def get_mode(approval_type: str) -> str:
    settings = get_settings()
    key = str(approval_type or "").strip().lower()
    return settings["modes"].get(key, settings["default_mode"])


def get_deadline_hours(approval_type: str) -> int:
    settings = get_settings()
    key = str(approval_type or "").strip().lower()
    return settings["deadlines_hours"].get(key, settings["default_deadline_hours"])


def is_off_allowed(approval_type: str) -> bool:
    return str(approval_type or "").strip().lower() in OFF_ALLOWLIST


def list_known_categories() -> list[str]:
    """Discover distinct approval_type values currently in the table."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT approval_type FROM approvals "
                "WHERE approval_type IS NOT NULL AND approval_type <> '' "
                "ORDER BY approval_type"
            ).fetchall()
        return [str(row["approval_type"]) for row in rows]
    except Exception:
        return []


__all__ = [
    "VALID_MODES",
    "OFF_ALLOWLIST",
    "DEFAULT_DEADLINE_HOURS",
    "get_settings",
    "save_settings",
    "get_mode",
    "get_deadline_hours",
    "is_off_allowed",
    "list_known_categories",
]
