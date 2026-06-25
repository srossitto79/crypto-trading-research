"""Operator profile API (Phase 6 / P6-T04).

- ``GET  /api/profile`` — returns ``{exists, structured, body, parse_error}``.
- ``PUT  /api/profile`` — partial update; merges ``structured`` and ``body``
  into the existing profile and writes back to ``USER.md``.

Behind ``require_operator_access``.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from axiom.api_security import require_operator_access
from axiom.workspace import (
    OperatorPreferences,
    OperatorProfile,
    read_operator_profile,
    write_operator_profile,
)


router = APIRouter(
    prefix="/api/profile",
    tags=["profile"],
    dependencies=[Depends(require_operator_access)],
)


_RISK_APPETITES = {"conservative", "balanced", "aggressive"}
_RESPONSE_STYLES = {"terse", "conversational", "verbose"}


class PreferencesPayload(BaseModel):
    notification_channels: list[str] | None = None
    quiet_hours: str | None = None
    risk_appetite: str | None = None
    response_style: str | None = None


class StructuredPayload(BaseModel):
    name: str | None = None
    timezone: str | None = None
    starting_capital_usd: float | None = None
    risk_per_trade_pct: float | None = None
    exchange: str | None = None
    asset_universe: str | None = None
    preferences: PreferencesPayload | None = None
    rules: list[str] | None = None


class ProfileUpdateBody(BaseModel):
    structured: StructuredPayload | None = None
    body: str | None = None


def _profile_to_response(profile: OperatorProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "exists": False,
            "structured": None,
            "body": "",
            "parse_error": None,
            "has_structured": False,
        }
    structured = {
        "name": profile.name,
        "timezone": profile.timezone,
        "starting_capital_usd": profile.starting_capital_usd,
        "risk_per_trade_pct": profile.risk_per_trade_pct,
        "exchange": profile.exchange,
        "asset_universe": profile.asset_universe,
        "preferences": asdict(profile.preferences),
        "rules": list(profile.rules),
    }
    return {
        "exists": True,
        "structured": structured,
        "body": profile.body,
        "parse_error": profile.parse_error,
        "has_structured": profile.has_structured,
    }


@router.get("")
def get_profile() -> dict[str, Any]:
    return _profile_to_response(read_operator_profile())


def _merge_preferences(current: OperatorPreferences, payload: PreferencesPayload | None) -> OperatorPreferences:
    if payload is None:
        return current
    new = OperatorPreferences(
        notification_channels=list(current.notification_channels),
        quiet_hours=current.quiet_hours,
        risk_appetite=current.risk_appetite,
        response_style=current.response_style,
    )
    if payload.notification_channels is not None:
        new.notification_channels = [c.strip() for c in payload.notification_channels if c and c.strip()]
    if payload.quiet_hours is not None:
        new.quiet_hours = payload.quiet_hours.strip() or None
    if payload.risk_appetite is not None:
        cleaned = payload.risk_appetite.strip().lower() or None
        if cleaned is not None and cleaned not in _RISK_APPETITES:
            raise HTTPException(status_code=422, detail=f"risk_appetite must be one of {sorted(_RISK_APPETITES)}")
        new.risk_appetite = cleaned
    if payload.response_style is not None:
        cleaned = payload.response_style.strip().lower() or None
        if cleaned is not None and cleaned not in _RESPONSE_STYLES:
            raise HTTPException(status_code=422, detail=f"response_style must be one of {sorted(_RESPONSE_STYLES)}")
        new.response_style = cleaned
    return new


@router.put("")
def put_profile(body: ProfileUpdateBody) -> dict[str, Any]:
    current = read_operator_profile() or OperatorProfile()

    if body.structured is not None:
        s = body.structured
        if s.name is not None:
            current.name = s.name.strip() or None
        if s.timezone is not None:
            current.timezone = s.timezone.strip() or None
        if s.starting_capital_usd is not None:
            current.starting_capital_usd = float(s.starting_capital_usd)
        if s.risk_per_trade_pct is not None:
            current.risk_per_trade_pct = float(s.risk_per_trade_pct)
        if s.exchange is not None:
            current.exchange = s.exchange.strip() or None
        if s.asset_universe is not None:
            current.asset_universe = s.asset_universe.strip() or None
        if s.preferences is not None:
            current.preferences = _merge_preferences(current.preferences, s.preferences)
        if s.rules is not None:
            current.rules = [r.strip() for r in s.rules if r and r.strip()]

    if body.body is not None:
        current.body = body.body

    write_operator_profile(current)
    return _profile_to_response(read_operator_profile())
