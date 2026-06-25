"""Custom strategy inventory helpers."""

from __future__ import annotations

import os
import re

_ARCHIVED_NAME_PATTERNS = (
    re.compile(r"^s\d{5}_", re.IGNORECASE),
    re.compile(r".*_s\d{5}$", re.IGNORECASE),
    re.compile(r".*_v\d+$", re.IGNORECASE),
)


def include_archived_custom_strategies() -> bool:
    raw = str(os.getenv("AXIOM_INCLUDE_ARCHIVED_CUSTOM_STRATEGIES", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def custom_strategy_status(module_name: str) -> str:
    normalized = str(module_name or "").strip()
    if not normalized or normalized == "__init__":
        return "ignored"
    for pattern in _ARCHIVED_NAME_PATTERNS:
        if pattern.match(normalized):
            return "archived"
    return "active"


__all__ = [
    "custom_strategy_status",
    "include_archived_custom_strategies",
]
