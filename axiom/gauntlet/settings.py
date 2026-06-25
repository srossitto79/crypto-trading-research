from __future__ import annotations

from typing import Any

from axiom.gauntlet.models import normalize_step_key


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _as_string_list(value: object, default: list[str]) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    else:
        items = default
    return [str(item).strip() for item in items if str(item).strip()]


def normalize_required_tests(value: object) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in _as_string_list(value, []):
        normalized = normalize_step_key(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def build_settings_snapshot() -> dict[str, Any]:
    from axiom.api_core import _load_pipeline_settings_payload
    from axiom.policy import load_pipeline_config

    pipeline_config = load_pipeline_config()
    pipeline_settings = _load_pipeline_settings_payload()

    quick_screen = dict(pipeline_config.get("quick_screen") or {})
    gauntlet = dict(pipeline_config.get("gauntlet") or {})
    gauntlet["required_tests"] = normalize_required_tests(gauntlet.get("required_tests"))

    return {
        "quick_screen": quick_screen,
        "gauntlet": gauntlet,
        "walk_forward": dict(pipeline_config.get("walk_forward") or {}),
        "robustness_thresholds": dict(pipeline_config.get("robustness_thresholds") or {}),
        "workflow": {
            "auto_quick_screen_enabled": _as_bool(
                pipeline_settings.get("gauntlet_auto_quick_screen_enabled"),
                True,
            ),
            "sweep_timeframes": _as_string_list(
                pipeline_settings.get("gate_sweep_timeframes"),
                ["15m", "1h", "4h", "1d"],
            ),
            "auto_approve_promotions": _as_bool(
                pipeline_settings.get("auto_approve_promotions"),
                False,
            ),
            "quick_screen_max_attempts": int(pipeline_settings.get("gauntlet_quick_screen_max_attempts") or 3),
            "step_stale_minutes": int(pipeline_settings.get("gauntlet_step_stale_minutes") or 30),
        },
        "pipeline_settings": pipeline_settings,
    }
