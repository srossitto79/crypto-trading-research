"""Load & validate research-source registries from Axiom settings."""
from __future__ import annotations

from typing import Any

from axiom.research_contract import get_research_sources_block


class RegistryError(ValueError):
    pass


SCHEMA: dict[str, dict[str, Any]] = {
    "reddit": {"list_key": "subs", "extra": {"client_id": (str, type(None)), "client_secret": (str, type(None))}},
    "blog":   {"list_key": "feeds", "extra": {}},
    "podcast": {"list_key": "feeds", "extra": {}},
    "github": {"list_key": "orgs",  "extra": {"personal_access_token": (str, type(None))}},
    "forum":  {"list_key": "sites", "extra": {}},
}


def _load_settings_block() -> dict[str, Any]:
    """Indirection so tests can monkeypatch without touching the DB."""
    return get_research_sources_block()


def resolve_registry(source_type: str) -> dict[str, Any] | None:
    """Return the resolved registry config for `source_type`, or None if disabled/absent.

    Raises RegistryError on malformed config (e.g. subs not list[str]).
    """
    block = _load_settings_block() or {}
    cfg = block.get(source_type)
    if not cfg or not cfg.get("enabled", False):
        return None
    schema = SCHEMA.get(source_type)
    if schema is None:
        raise RegistryError(f"unknown source_type: {source_type}")
    list_key = schema["list_key"]
    items = cfg.get(list_key, [])
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise RegistryError(f"{source_type}.{list_key} must be list[str]")
    try:
        rate = int(cfg.get("rate_limit_per_min", 30))
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{source_type}.rate_limit_per_min must be int") from exc
    if rate <= 0:
        raise RegistryError(f"{source_type}.rate_limit_per_min must be > 0")
    out: dict[str, Any] = {list_key: list(items), "rate_limit_per_min": rate}
    for k, allowed_types in schema["extra"].items():
        v = cfg.get(k)
        if not isinstance(v, allowed_types):
            raise RegistryError(f"{source_type}.{k} has wrong type")
        out[k] = v
    return out
