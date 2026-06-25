"""Read-only strategy sourcing for Regime Lab."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import axiom.config as cfg
from axiom.strategies.backtest import expand_strategy_trade_modes, validate_backtest_risk_controls
from axiom.strategies.base import BaseStrategy
from axiom.strategies.registry import build_strategy_from_row
from axiom.util import normalize_stage

log = logging.getLogger("axiom.lab_strategy_pool")

LAB_STRATEGY_SOURCE_REGISTRY = "registry"
LAB_STRATEGY_SOURCE_ACTIVE = "active"
LAB_STRATEGY_SOURCE_PAPER = "paper"
LAB_STRATEGY_SOURCE_BACKTESTING = "backtesting"
LAB_STRATEGY_SOURCE_GRAVEYARD = "graveyard"
LAB_STRATEGY_SOURCE_ALL_MANAGED = "all_managed"

VALID_LAB_STRATEGY_SOURCES = {
    LAB_STRATEGY_SOURCE_REGISTRY,
    LAB_STRATEGY_SOURCE_ACTIVE,
    LAB_STRATEGY_SOURCE_PAPER,
    LAB_STRATEGY_SOURCE_BACKTESTING,
    LAB_STRATEGY_SOURCE_GRAVEYARD,
    LAB_STRATEGY_SOURCE_ALL_MANAGED,
}

DEFAULT_LAB_STRATEGY_SOURCES = [
    LAB_STRATEGY_SOURCE_ACTIVE,
    LAB_STRATEGY_SOURCE_REGISTRY,
    LAB_STRATEGY_SOURCE_GRAVEYARD,
]


def _supports_vectorized_signals(strategy: BaseStrategy) -> bool:
    return type(strategy).generate_signals is not BaseStrategy.generate_signals


def _expand_trade_mode_variants(strategy: BaseStrategy, base_candidate: dict[str, Any]) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    modes = expand_strategy_trade_modes(
        strategy_type=str(strategy.strategy_type),
        params=dict(strategy.params or {}),
        strategy_obj=strategy,
    )
    for trade_mode in modes:
        candidate = dict(base_candidate)
        candidate["trade_mode"] = trade_mode
        candidate["position_model"] = "hedged" if trade_mode == "both" else "single_side"
        candidate["candidate_key"] = f"{candidate['strategy_id']}:{trade_mode}"
        display_name = str(candidate.get("display_name") or candidate.get("strategy_id") or "").strip()
        if display_name and trade_mode != "long_only":
            candidate["display_name"] = f"{display_name} [{trade_mode}]"
        variants.append(candidate)
    return variants or [dict(base_candidate)]


@contextmanager
def get_main_db_readonly() -> sqlite3.Connection:
    """Open the production DB in read-only mode for lab sourcing."""
    db_path = Path(cfg.AXIOM_DB).expanduser().resolve()
    uri = f"{db_path.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def normalize_strategy_sources(strategy_sources: list[str] | None) -> list[str]:
    """Normalize user-provided strategy source names into a stable ordered list."""
    requested = strategy_sources or DEFAULT_LAB_STRATEGY_SOURCES
    normalized: list[str] = []
    seen: set[str] = set()
    for value in requested:
        source = str(value or "").strip().lower()
        if not source or source not in VALID_LAB_STRATEGY_SOURCES or source in seen:
            continue
        normalized.append(source)
        seen.add(source)
    return normalized or list(DEFAULT_LAB_STRATEGY_SOURCES)


def list_strategy_pool_candidates(
    *,
    strategy_sources: list[str] | None = None,
    strategy_ids: list[str] | None = None,
    max_strategies: int | None = None,
    persist_quarantine: bool = False,
) -> list[dict[str, Any]]:
    """Return managed strategy candidates from the requested pools."""
    report = inspect_strategy_pool(
        strategy_sources=strategy_sources,
        strategy_ids=strategy_ids,
        max_strategies=max_strategies,
        persist_quarantine=persist_quarantine,
    )
    return list(report["included"])


def inspect_strategy_pool(
    *,
    strategy_sources: list[str] | None = None,
    strategy_ids: list[str] | None = None,
    max_strategies: int | None = None,
    persist_quarantine: bool = False,
) -> dict[str, Any]:
    """Inspect included and skipped managed strategy candidates for lab reporting."""
    sources = normalize_strategy_sources(strategy_sources)
    include_all_managed = LAB_STRATEGY_SOURCE_ALL_MANAGED in sources
    id_filter = {str(value).strip() for value in (strategy_ids or []) if str(value).strip()}
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if not Path(cfg.AXIOM_DB).exists():
        return {
            "requested_sources": list(sources),
            "included": [],
            "skipped": [],
            "counts": {"included": 0, "skipped": 0},
        }

    try:
        with get_main_db_readonly() as conn:
            strategy_rows = conn.execute("SELECT * FROM strategies ORDER BY updated_at DESC, created_at DESC, id").fetchall()
            archived_rows = conn.execute(
                "SELECT id, original_data, archived_at, archived_by, reason FROM archived_strategies ORDER BY archived_at DESC, id"
            ).fetchall()
    except sqlite3.OperationalError as exc:
        log.warning("Regime Lab could not read strategy pools from production DB: %s", exc)
        return {
            "requested_sources": list(sources),
            "included": [],
            "skipped": [{"strategy_id": "<db_read_error>", "reason": str(exc), "source_pool": "system"}],
            "counts": {"included": 0, "skipped": 1},
        }

    for raw_row in strategy_rows:
        row = dict(raw_row)
        if not _row_matches_sources(row, sources, include_all_managed=include_all_managed):
            continue
        source_hint = _infer_primary_source(row)
        candidate, skip_reason, strategy = _candidate_from_row(row, source_hint=source_hint)
        if candidate is None:
            skipped.append(
                {
                    "strategy_id": str(row.get("id") or "<unknown>"),
                    "display_name": str(row.get("name") or row.get("id") or "<unknown>"),
                    "source_pool": source_hint,
                    "source_stage": _normalized_stage_for_row(row),
                    "reason": skip_reason or "unknown",
                }
            )
            continue
        for variant in _expand_trade_mode_variants(strategy, candidate):
            sid = str(variant["strategy_id"])
            candidate_key = str(variant.get("candidate_key") or sid)
            if id_filter and sid not in id_filter and candidate_key not in id_filter:
                continue
            if candidate_key in seen_ids:
                continue
            rows.append(variant)
            seen_ids.add(candidate_key)

    for archived_row in archived_rows:
        if not include_all_managed and LAB_STRATEGY_SOURCE_GRAVEYARD not in sources:
            break
        row = _archived_row_to_strategy_row(dict(archived_row))
        if row is None:
            skipped.append(
                {
                    "strategy_id": str(archived_row["id"]),
                    "display_name": str(archived_row["id"]),
                    "source_pool": LAB_STRATEGY_SOURCE_GRAVEYARD,
                    "source_stage": "archived",
                    "reason": "invalid_archived_payload",
                }
            )
            continue
        candidate, skip_reason, strategy = _candidate_from_row(row, source_hint=LAB_STRATEGY_SOURCE_GRAVEYARD)
        if candidate is None:
            skipped.append(
                {
                    "strategy_id": str(row.get("id") or "<unknown>"),
                    "display_name": str(row.get("name") or row.get("id") or "<unknown>"),
                    "source_pool": LAB_STRATEGY_SOURCE_GRAVEYARD,
                    "source_stage": _normalized_stage_for_row(row),
                    "reason": skip_reason or "unknown",
                }
            )
            continue
        for variant in _expand_trade_mode_variants(strategy, candidate):
            sid = str(variant["strategy_id"])
            candidate_key = str(variant.get("candidate_key") or sid)
            if id_filter and sid not in id_filter and candidate_key not in id_filter:
                continue
            if candidate_key in seen_ids:
                continue
            rows.append(variant)
            seen_ids.add(candidate_key)

    # Log summary of skipped strategies instead of per-strategy warnings
    unregistered_skips: list[str] = []
    ambiguous_skips: list[str] = []
    for skip in skipped:
        reason = str(skip.get("reason") or "")
        sid = str(skip.get("strategy_id") or "<unknown>")
        if "is not registered" in reason:
            unregistered_skips.append(sid)
        elif "ambiguous runtime type" in reason:
            ambiguous_skips.append(sid)
    if unregistered_skips:
        log.warning(
            "Skipped %d strategies with unregistered runtime types: %s",
            len(unregistered_skips),
            ", ".join(unregistered_skips[:5])
            + (f" and {len(unregistered_skips) - 5} more" if len(unregistered_skips) > 5 else ""),
        )
    if ambiguous_skips:
        log.warning(
            "Skipped %d strategies with ambiguous runtime types: %s",
            len(ambiguous_skips),
            ", ".join(ambiguous_skips[:5])
            + (f" and {len(ambiguous_skips) - 5} more" if len(ambiguous_skips) > 5 else ""),
        )

    # Strategy-loss visibility fix (lab-quarantine-persist): persist each
    # quarantine/skip as a queryable row so an operator can later answer
    # "which custom strategies were quarantined, why, and did a fixed one
    # recover?" — previously the reason only reached worker logs. Reuses the
    # existing lab_selection_event sink (blocked_reason + decision_json, no
    # job_id/symbol FK requirements) rather than adding a new table/migration.
    # Off by default so read-only API inspection (and the per-source matrix
    # engine path) do not write rows; planning/selection callers opt in.
    if persist_quarantine and skipped:
        _persist_quarantine_events(skipped)

    rows.sort(
        key=lambda item: (
            _source_priority(str(item.get("source_pool") or "")),
            _stage_priority(str(item.get("source_stage") or "")),
            -_sort_epoch(item.get("updated_at")),
            str(item.get("strategy_id") or ""),
        )
    )
    if max_strategies is not None:
        rows = rows[: max(1, int(max_strategies))]
    return {
        "requested_sources": list(sources),
        "included": rows,
        "skipped": skipped,
        "counts": {"included": len(rows), "skipped": len(skipped)},
    }


def _persist_quarantine_events(skipped: list[dict[str, Any]]) -> None:
    """Record each skipped/quarantined strategy as a lab_selection_event row.

    Strategy-loss visibility fix (lab-quarantine-persist). Each row carries the
    strategy id (champion_strategy_id), the reason (blocked_reason) and the
    timestamp (created_at), making quarantines queryable without a bespoke
    table. Best-effort: persistence failures must never break pool sourcing.
    Sentinel symbol/timeframe keep these audit rows segregated from real
    per-symbol selections (which are only ever fetched by id, never scanned).
    """
    try:
        from axiom.lab_db import create_selection_event, existing_quarantine_event_keys
    except Exception as exc:  # pragma: no cover - import guard
        log.warning("lab-quarantine-persist: could not import selection-event sink: %s", exc)
        return

    # Dedup against already-open quarantines so the same (strategy, reason) isn't
    # re-logged every discovery cycle (would grow the table unboundedly and
    # drown the audit). A new reason for the same strategy is still recorded.
    try:
        seen = existing_quarantine_event_keys()
    except Exception:
        seen = set()

    for skip in skipped:
        strategy_id = str(skip.get("strategy_id") or "<unknown>")
        reason = str(skip.get("reason") or "unknown")
        blocked_reason = f"quarantine:{reason}"
        if (strategy_id, blocked_reason) in seen:
            continue
        try:
            create_selection_event(
                symbol="<lab_pool>",
                timeframe="-",
                regime=None,
                confidence=0.0,
                champion_strategy_id=strategy_id,
                blocked_reason=blocked_reason,
                decision_json={
                    "kind": "quarantine",
                    "strategy_id": strategy_id,
                    "display_name": str(skip.get("display_name") or strategy_id),
                    "source_pool": str(skip.get("source_pool") or ""),
                    "source_stage": str(skip.get("source_stage") or ""),
                    "reason": reason,
                },
            )
            seen.add((strategy_id, blocked_reason))
        except Exception as exc:  # never let audit persistence break sourcing
            log.warning(
                "lab-quarantine-persist: failed to record quarantine for %s: %s",
                strategy_id,
                exc,
            )


def _source_priority(source: str) -> int:
    order = {
        LAB_STRATEGY_SOURCE_ACTIVE: 0,
        LAB_STRATEGY_SOURCE_PAPER: 1,
        LAB_STRATEGY_SOURCE_BACKTESTING: 2,
        LAB_STRATEGY_SOURCE_GRAVEYARD: 3,
        LAB_STRATEGY_SOURCE_ALL_MANAGED: 4,
    }
    return order.get(str(source).strip().lower(), 99)


def _stage_priority(stage: str) -> int:
    order = {
        "live_graduated": 0,
        "paper": 1,
        "gauntlet": 2,
        "rejected": 3,
        "archived": 4,
    }
    return order.get(str(stage).strip().lower(), 50)


def _sort_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _normalized_stage_for_row(row: dict[str, Any]) -> str:
    return normalize_stage(row.get("stage") or row.get("status"))


def _infer_primary_source(row: dict[str, Any]) -> str:
    stage = _normalized_stage_for_row(row)
    if stage == "paper":
        return LAB_STRATEGY_SOURCE_ACTIVE
    if stage == "live_graduated":
        return LAB_STRATEGY_SOURCE_ACTIVE
    if stage == "gauntlet":
        return LAB_STRATEGY_SOURCE_BACKTESTING
    if stage in {"archived", "rejected"}:
        return LAB_STRATEGY_SOURCE_GRAVEYARD
    return LAB_STRATEGY_SOURCE_ALL_MANAGED


def _row_matches_sources(
    row: dict[str, Any],
    sources: list[str],
    *,
    include_all_managed: bool,
) -> bool:
    if include_all_managed:
        return True

    stage = _normalized_stage_for_row(row)
    source_set = set(sources)
    if LAB_STRATEGY_SOURCE_ACTIVE in source_set and stage in {"paper", "live_graduated"}:
        return True
    if LAB_STRATEGY_SOURCE_PAPER in source_set and stage == "paper":
        return True
    if LAB_STRATEGY_SOURCE_BACKTESTING in source_set and stage == "gauntlet":
        return True
    if LAB_STRATEGY_SOURCE_GRAVEYARD in source_set and stage in {"archived", "rejected"}:
        return True
    return False


def _archived_row_to_strategy_row(row: dict[str, Any]) -> dict[str, Any] | None:
    raw_original = row.get("original_data")
    if isinstance(raw_original, str):
        try:
            original = json.loads(raw_original or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
    elif isinstance(raw_original, dict):
        original = dict(raw_original)
    else:
        return None

    if not isinstance(original, dict):
        return None

    strategy_id = str(original.get("id") or row.get("id") or "").strip()
    if not strategy_id:
        return None

    merged = dict(original)
    merged["id"] = strategy_id
    merged["stage"] = str(original.get("stage") or original.get("status") or "archived")
    merged["status"] = str(original.get("status") or original.get("stage") or "archived")
    merged.setdefault("notes", row.get("reason"))
    return merged


def _candidate_from_row(
    row: dict[str, Any],
    *,
    source_hint: str,
) -> tuple[dict[str, Any] | None, str | None, BaseStrategy | None]:
    try:
        strategy = build_strategy_from_row(row)
    except Exception as exc:
        return None, str(exc), None

    stage = _normalized_stage_for_row(row)
    params = dict(strategy.params or {})
    compatibility_warning = validate_backtest_risk_controls(params)
    if compatibility_warning:
        return None, compatibility_warning, None
    return (
        {
            "strategy_id": str(strategy.strategy_id),
            "strategy_type": str(strategy.strategy_type),
            "params": params,
            "supports_vectorized_signals": _supports_vectorized_signals(strategy),
            "source_pool": str(source_hint),
            "source_stage": stage,
            "display_name": str(row.get("name") or strategy.name or strategy.strategy_id),
            "updated_at": str(row.get("updated_at") or row.get("archived_at") or row.get("created_at") or ""),
        },
        None,
        strategy,
    )
