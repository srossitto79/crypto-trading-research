"""Shared helpers for the operator-managed system pause state."""

from __future__ import annotations

import logging
from typing import Any

from axiom.db import _now, kv_get, kv_set

log = logging.getLogger(__name__)

_SYSTEM_STATE_KEY = "system_state"
_LEGACY_SYSTEM_PAUSED_KEY = "system_paused"

MODE_MANUAL = "manual"
MODE_SEMI_AUTO = "semi_auto"
MODE_AUTO = "auto"
VALID_MODES = (MODE_MANUAL, MODE_SEMI_AUTO, MODE_AUTO)

# Fresh installs default to manual — safer until the operator explicitly enables
# autonomous behavior.
_DEFAULT_MODE = MODE_MANUAL


def _mode_to_flags(mode: str) -> tuple[bool, bool]:
    """Return (system_paused, generation_paused) for a given mode.

    NOTE: `system_paused` here is only consulted by legacy `_flags_to_mode`
    fallback lookup when a mode value has never been persisted. Actual mode
    transitions (see `set_system_mode`) must NOT overwrite the live
    `paused` flag — that flag is operator-driven via stop/start/reset-halt
    and is independent of pipeline autonomy mode.
    """
    if mode == MODE_AUTO:
        return (False, False)
    if mode == MODE_SEMI_AUTO:
        return (False, True)
    return (False, True)


def _flags_to_mode(system_paused: bool, generation_paused: bool) -> str:
    if generation_paused:
        # Fall back to semi_auto when only the generation flag was ever
        # persisted; manual mode is only selectable via explicit system_mode.
        return MODE_SEMI_AUTO
    return MODE_AUTO


def _effective_stored_mode(state: dict[str, Any]) -> str | None:
    """Return the mode the operator would currently observe, if determinable.

    Mirrors ``get_system_pause_state``: an explicitly persisted mode wins; a
    completely untouched state reports the safe default (manual). Returns None
    only for legacy states where flags were persisted but a mode never was —
    callers then fall back to deriving a mode from the flags.
    """
    stored = _normalize_mode(state.get("system_mode"))
    if stored is not None:
        return stored
    if "paused" not in state and "generation_paused" not in state:
        return _DEFAULT_MODE
    return None


def _normalize_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    if normalized in VALID_MODES:
        return normalized
    return None


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
    return bool(default)


def _load_system_state() -> dict[str, Any]:
    raw = kv_get(_SYSTEM_STATE_KEY, {}) or {}
    return raw if isinstance(raw, dict) else {}


def get_system_pause_state() -> dict[str, Any]:
    """Return the canonical pause state while still honoring the legacy flag."""
    state = _load_system_state()
    if "paused" in state:
        paused = _coerce_bool(state.get("paused"), False)
    else:
        paused = _coerce_bool(kv_get(_LEGACY_SYSTEM_PAUSED_KEY, False), False)
    paused_at = state.get("paused_at")
    generation_paused = _coerce_bool(state.get("generation_paused"), False)
    generation_paused_at = state.get("generation_paused_at")
    stored_mode = _normalize_mode(state.get("system_mode"))
    derived_mode = stored_mode or _flags_to_mode(paused, generation_paused)
    # If nothing has ever been written, fall back to the safe default.
    if stored_mode is None and "paused" not in state and "generation_paused" not in state:
        derived_mode = _DEFAULT_MODE
        paused, generation_paused = _mode_to_flags(derived_mode)
    return {
        "paused": paused,
        "paused_at": str(paused_at) if paused and paused_at else None,
        "generation_paused": generation_paused,
        "generation_paused_at": (
            str(generation_paused_at)
            if generation_paused and generation_paused_at
            else None
        ),
        "system_mode": derived_mode,
        "system_mode_at": state.get("system_mode_at"),
    }


def is_system_paused() -> bool:
    return bool(get_system_pause_state().get("paused"))


def is_generation_paused() -> bool:
    return bool(get_system_pause_state().get("generation_paused"))


def get_system_mode() -> str:
    return str(get_system_pause_state().get("system_mode") or _DEFAULT_MODE)


def is_autonomy_paused() -> bool:
    """True when the scheduler should skip autonomous jobs entirely (manual mode)."""
    return get_system_mode() == MODE_MANUAL


def set_system_paused(paused: bool, *, paused_at: str | None = None) -> dict[str, Any]:
    """Persist the canonical pause state and bridge the legacy key during rollout.

    The trading halt flag is orthogonal to the pipeline autonomy mode (see
    ``set_system_mode``). Toggling stop/start MUST NOT rewrite the mode: the
    legacy ``_flags_to_mode`` derivation can only produce semi_auto/auto, so
    recomputing it here silently flipped a manually-frozen system out of
    manual and thawed the entire frozen backlog (B-28). The stored mode is
    preserved; flag-derivation only happens for legacy states that never
    persisted a mode.
    """
    normalized_paused = bool(paused)
    state = _load_system_state()
    stored_mode = _effective_stored_mode(state)
    previous_mode = stored_mode or _flags_to_mode(
        _coerce_bool(state.get("paused"), False),
        _coerce_bool(state.get("generation_paused"), False),
    )
    if normalized_paused:
        normalized_paused_at = str(paused_at or state.get("paused_at") or _now()).strip() or _now()
    else:
        normalized_paused_at = None
    state["paused"] = normalized_paused
    state["paused_at"] = normalized_paused_at
    if stored_mode is not None:
        state["system_mode"] = stored_mode
    else:
        generation_paused = _coerce_bool(state.get("generation_paused"), False)
        derived = _flags_to_mode(normalized_paused, generation_paused)
        log.warning(
            "set_system_paused: no stored system mode — deriving %r from pause flags",
            derived,
        )
        state["system_mode"] = derived
    if state["system_mode"] != previous_mode:
        state["system_mode_at"] = normalized_paused_at or _now()
    else:
        state["system_mode_at"] = state.get("system_mode_at") or normalized_paused_at or _now()
    kv_set(_SYSTEM_STATE_KEY, state)
    kv_set(_LEGACY_SYSTEM_PAUSED_KEY, normalized_paused)
    from axiom.system_mode_policy import sync_manual_mode_transition

    sync_manual_mode_transition(previous_mode=previous_mode, current_mode=state["system_mode"])
    return {
        "paused": normalized_paused,
        "paused_at": normalized_paused_at,
    }


def set_generation_paused(
    paused: bool,
    *,
    paused_at: str | None = None,
) -> dict[str, Any]:
    """Persist the strategy-generation pause state.

    The generation toggle legitimately moves the mode between semi_auto
    (paused) and auto (resumed) — that pair IS the generation flag. It must
    never escalate OUT of manual, though: manual is an explicit operator
    freeze, and ``_flags_to_mode`` can't represent it, so the old recompute
    silently flipped manual to FULL AUTO on resume-generation and thawed the
    frozen backlog (B-28). In manual mode the toggle is refused conservatively:
    mode stays manual and generation stays paused; the operator must change
    modes explicitly (``set_system_mode``) to re-enable autonomy.
    """
    normalized_paused = bool(paused)
    state = _load_system_state()
    stored_mode = _effective_stored_mode(state)
    previous_mode = stored_mode or _flags_to_mode(
        _coerce_bool(state.get("paused"), False),
        _coerce_bool(state.get("generation_paused"), False),
    )
    if stored_mode == MODE_MANUAL and not normalized_paused:
        log.warning(
            "resume-generation requested while system mode is manual — keeping "
            "manual mode and generation paused (use set_system_mode to leave "
            "manual explicitly)"
        )
        normalized_paused = True
        paused_at = paused_at or state.get("generation_paused_at")
    if normalized_paused:
        normalized_paused_at = (
            str(paused_at or state.get("generation_paused_at") or _now()).strip()
            or _now()
        )
    else:
        normalized_paused_at = None
    state["generation_paused"] = normalized_paused
    state["generation_paused_at"] = normalized_paused_at
    if stored_mode == MODE_MANUAL:
        state["system_mode"] = MODE_MANUAL
        state["system_mode_at"] = state.get("system_mode_at") or _now()
    else:
        system_paused = _coerce_bool(state.get("paused"), False)
        state["system_mode"] = _flags_to_mode(system_paused, normalized_paused)
        state["system_mode_at"] = normalized_paused_at or state.get("system_mode_at") or _now()
    kv_set(_SYSTEM_STATE_KEY, state)
    from axiom.system_mode_policy import sync_manual_mode_transition

    sync_manual_mode_transition(previous_mode=previous_mode, current_mode=state["system_mode"])
    return {
        "generation_paused": normalized_paused,
        "generation_paused_at": normalized_paused_at,
    }


def set_system_mode(mode: str, *, changed_at: str | None = None) -> dict[str, Any]:
    """Persist the top-level pipeline autonomy mode.

    Pipeline mode (manual/semi_auto/auto) is orthogonal to the trading halt
    flag. Switching modes MUST NOT touch `paused` — that flag is operator-
    driven via stop_system/start_system/reset_trading_halt. Autonomous job
    scheduling is gated by mode directly (`is_autonomy_paused()`), not by
    the shared pause flag.
    """
    normalized = _normalize_mode(mode)
    if normalized is None:
        raise ValueError(
            f"invalid system mode: {mode!r} (expected one of {VALID_MODES})"
        )
    _, generation_paused = _mode_to_flags(normalized)
    state = _load_system_state()
    previous_mode = _normalize_mode(state.get("system_mode")) or _flags_to_mode(
        _coerce_bool(state.get("paused"), False),
        _coerce_bool(state.get("generation_paused"), False),
    )
    timestamp = str(changed_at or _now()).strip() or _now()

    system_paused = _coerce_bool(state.get("paused"), False)

    state["system_mode"] = normalized
    state["system_mode_at"] = timestamp
    state["generation_paused"] = generation_paused
    state["generation_paused_at"] = timestamp if generation_paused else None

    kv_set(_SYSTEM_STATE_KEY, state)
    from axiom.system_mode_policy import sync_manual_mode_transition

    sync_manual_mode_transition(previous_mode=previous_mode, current_mode=normalized)
    return {
        "system_mode": normalized,
        "system_mode_at": timestamp,
        "paused": system_paused,
        "paused_at": state.get("paused_at"),
        "generation_paused": generation_paused,
        "generation_paused_at": state["generation_paused_at"],
    }


__all__ = [
    "MODE_AUTO",
    "MODE_MANUAL",
    "MODE_SEMI_AUTO",
    "VALID_MODES",
    "get_system_mode",
    "get_system_pause_state",
    "is_autonomy_paused",
    "is_generation_paused",
    "is_system_paused",
    "set_generation_paused",
    "set_system_mode",
    "set_system_paused",
]
