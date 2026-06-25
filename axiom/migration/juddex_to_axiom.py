"""One-shot migration: move ~/.juddex -> ~/.axiom on first Axiom boot.

Idempotent: if ~/.axiom already exists with content, skip. If ~/.juddex
does not exist, skip. Otherwise move the directory, rename juddex.duckdb
to axiom.duckdb, rename .juddex_key to .axiom_key, and drop a
LEGACY_JUddEX_MOVED_TO_AXIOM breadcrumb in the old location if it still exists.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

BREADCRUMB = "LEGACY_JUddEX_MOVED_TO_AXIOM"


def migrate_home_directory() -> bool:
    """Return True if a migration occurred, False if skipped."""
    home = Path.home()
    legacy = home / ".juddex"
    current = home / ".axiom"

    if current.exists() and any(current.iterdir()):
        return False
    if not legacy.exists():
        return False

    log.warning("axiom: migrating %s -> %s (one-shot)", legacy, current)
    legacy.rename(current)

    for old_name, new_name in (
        ("juddex.duckdb", "axiom.duckdb"),
        (".juddex_key", ".axiom_key"),
    ):
        old_path = current / old_name
        new_path = current / new_name
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)

    try:
        (home / ".juddex").mkdir(exist_ok=True)
        (home / ".juddex" / BREADCRUMB).write_text(
            f"This directory's contents were moved to {current} during the "
            f"Juddex -> Axiom rename. Safe to delete.\n"
        )
    except OSError:
        pass

    return True
