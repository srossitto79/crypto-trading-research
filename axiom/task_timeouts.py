"""Shared timeout helpers for agent tasks and stale recovery."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import Any

# ---- Derived defaults -------------------------------------------------------
# Agent task timeouts are derived from AXIOM_AI_READ_TIMEOUT_SECONDS so that
# users who configure a slow local LLM (long per-request read timeout) get
# proportionally longer wall-clock budgets for the full multi-round agent loop.
#
# Formula: agent  = read_timeout * MIN_AGENT_ROUNDS  (at least 3 rounds)
#          brain  = same as agent
#          backtest = read_timeout * MIN_BACKTEST_ROUNDS  (at least 6)
#          max    = read_timeout * MAX_TOOL_ROUNDS  (must fit 30 rounds)
#
# With default read_timeout=120 the derived values match the original hardcoded
# 900 / 1800 / 7200.  With read_timeout=900 (local LLM) they become
# 2700 / 5400 / 27000.
_MIN_AGENT_ROUNDS = 3
_MIN_BACKTEST_ROUNDS = 6
_MAX_TOOL_ROUNDS = 30


def _effective_read_timeout() -> int:
    """Read the per-HTTP-request read timeout from env (same source as ai.py)."""
    return int(float(os.environ.get("AXIOM_AI_READ_TIMEOUT_SECONDS", "120.0")))


_READ_TIMEOUT = _effective_read_timeout()
DEFAULT_AGENT_TASK_TIMEOUT_SECONDS = max(900, _READ_TIMEOUT * _MIN_AGENT_ROUNDS)
DEFAULT_BACKTEST_AGENT_TASK_TIMEOUT_SECONDS = max(1800, _READ_TIMEOUT * _MIN_BACKTEST_ROUNDS)
DEFAULT_BRAIN_TASK_TIMEOUT_SECONDS = DEFAULT_AGENT_TASK_TIMEOUT_SECONDS
MIN_TASK_TIMEOUT_SECONDS = 60
MAX_TASK_TIMEOUT_SECONDS = max(7200, _READ_TIMEOUT * _MAX_TOOL_ROUNDS)
MAX_STALE_RECOVERY_MINUTES = 240
REAPER_GRACE_MINUTES = 1
STALE_RECOVERY_GRACE_MINUTES = 5


def coerce_timeout_seconds(
    value: object,
    default: int,
    *,
    minimum: int = MIN_TASK_TIMEOUT_SECONDS,
    maximum: int = MAX_TASK_TIMEOUT_SECONDS,
) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except Exception:
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def resolve_agent_task_timeout_seconds(
    task_type: str,
    *,
    settings: Mapping[str, Any] | None = None,
) -> int:
    config = settings if isinstance(settings, Mapping) else {}
    default_timeout = coerce_timeout_seconds(
        config.get("agent_task_timeout_seconds"),
        DEFAULT_AGENT_TASK_TIMEOUT_SECONDS,
    )
    backtest_timeout = coerce_timeout_seconds(
        config.get("backtest_agent_task_timeout_seconds"),
        max(default_timeout, DEFAULT_BACKTEST_AGENT_TASK_TIMEOUT_SECONDS),
    )
    lowered = str(task_type or "").strip().lower()
    if lowered in {"backtest", "simulation", "robustness"}:
        return backtest_timeout
    return default_timeout


def resolve_brain_task_timeout_seconds(
    *,
    settings: Mapping[str, Any] | None = None,
) -> int:
    """Resolve the wall-clock budget for one Brain cycle.

    Honours ``brain_task_timeout_seconds`` in settings, then falls back to the
    shared agent-task default. Clamped to the same [MIN, MAX] bounds as every
    other task timeout so it can never be configured below a single step.
    """
    config = settings if isinstance(settings, Mapping) else {}
    return coerce_timeout_seconds(
        config.get("brain_task_timeout_seconds"),
        DEFAULT_BRAIN_TASK_TIMEOUT_SECONDS,
    )


def max_agent_task_timeout_seconds(settings: Mapping[str, Any] | None = None) -> int:
    return max(
        resolve_agent_task_timeout_seconds("research", settings=settings),
        resolve_agent_task_timeout_seconds("backtest", settings=settings),
    )


def recommended_agent_reaper_timeout_minutes(
    settings: Mapping[str, Any] | None = None,
    *,
    grace_minutes: int = REAPER_GRACE_MINUTES,
) -> int:
    timeout_seconds = max_agent_task_timeout_seconds(settings)
    return max(1, math.ceil(timeout_seconds / 60.0) + max(0, int(grace_minutes)))


def recommended_stale_recovery_minutes(
    settings: Mapping[str, Any] | None = None,
    *,
    grace_minutes: int = STALE_RECOVERY_GRACE_MINUTES,
) -> int:
    return recommended_agent_reaper_timeout_minutes(settings) + max(0, int(grace_minutes))


def coerce_stale_recovery_minutes(
    value: object,
    *,
    settings: Mapping[str, Any] | None = None,
    maximum: int = MAX_STALE_RECOVERY_MINUTES,
) -> int:
    baseline = recommended_stale_recovery_minutes(settings)
    try:
        parsed = int(float(str(value).strip()))
    except Exception:
        parsed = baseline
    clamped = max(1, min(max(int(maximum), baseline), parsed))
    return max(baseline, clamped)

