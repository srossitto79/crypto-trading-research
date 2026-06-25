#!/usr/bin/env python3
"""Synchronize scanner defaults into SQLite strategy rows."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from axiom.db import get_db, init_db
from axiom.scanner import STRATEGIES


VALID_STATUSES = {"researching", "backtesting", "paper", "deployed", "retired", "rejected", "trash"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if not normalized:
        raise ValueError("status is required")
    if normalized not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Use one of: {', '.join(sorted(VALID_STATUSES))}"
        )
    return normalized


def _json_or_none(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _sync_to_db(status: str, dry_run: bool = False) -> tuple[int, int]:
    status = _normalize_status(status)
    now = _now()
    updated = 0
    inserted = 0

    for strategy_id, spec in STRATEGIES.items():
        if not isinstance(spec, dict):
            print(f"[skip] {strategy_id}: invalid strategy spec")
            continue

        name = str(spec.get("name") or strategy_id).strip()
        strategy_type = str(spec.get("type") or "scanner").strip()
        symbol = str(spec.get("asset") or "").strip()
        timeframe = str(spec.get("timeframe") or "1h").strip().lower() or "1h"
        params = _json_or_none(spec.get("params")) or "{}"
        metrics = _json_or_none(
            {
                "fitness_v1": spec.get("fitness_v1"),
                "fitness_v2": spec.get("fitness_v2"),
            }
        )

        with get_db() as conn:
            row = conn.execute("SELECT created_at FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
            if row:
                if dry_run:
                    print(f"[dry-run] Update strategy {strategy_id} -> status={status}")
                else:
                    conn.execute(
                        """
                        UPDATE strategies
                        SET name = ?, type = ?, symbol = ?, timeframe = ?, params = ?, metrics = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (name, strategy_type, symbol, timeframe, params, metrics, status, now, strategy_id),
                    )
                updated += 1
            else:
                if dry_run:
                    print(f"[dry-run] Insert strategy {strategy_id} -> status={status}")
                else:
                    conn.execute(
                        """
                        INSERT INTO strategies
                        (id, name, type, symbol, timeframe, params, metrics, status, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            strategy_id,
                            name,
                            strategy_type,
                            symbol,
                            timeframe,
                            params,
                            metrics,
                            status,
                            "synced from scanner defaults",
                            now,
                            now,
                        ),
                    )
                inserted += 1

    return updated, inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync scanner default STRATEGIES into the strategies table.")
    parser.add_argument(
        "--status",
        default="paper",
        choices=sorted(VALID_STATUSES),
        help="Strategy status to write for synced strategies (default: paper).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned inserts/updates without writing to SQLite.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    updated, inserted = _sync_to_db(args.status, dry_run=args.dry_run)
    summary = f"Scanner defaults sync complete. Updated: {updated}, inserted: {inserted}"
    if args.dry_run:
        print(f"[dry-run] {summary}")
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
