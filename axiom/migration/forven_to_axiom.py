"""One-shot migration: move ~/.forven -> ~/.axiom on first Axiom boot.

Idempotent: if ~/.axiom already exists with content, skip. If ~/.forven
does not exist, skip. Otherwise move the directory, rename forven.db
to axiom.db, rename .forven_key to .axiom_key, and drop a
FORVEN_MOVED_TO_AXIOM breadcrumb in the old location if it still exists.

This runs AFTER juddex_to_axiom, so if both .juddex and .forven exist,
the juddex migration runs first (moving .juddex -> .axiom), and this one
will see .axiom already populated and skip. This is correct: juddex is
the older format and should take priority.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

BREADCRUMB = "FORVEN_MOVED_TO_AXIOM"


def migrate_home_directory() -> bool:
    """Return True if a migration occurred, False if skipped."""
    home = Path.home()
    legacy = home / ".forven"
    current = home / ".axiom"

    if current.exists() and any(current.iterdir()):
        return False
    if not legacy.exists():
        return False

    log.warning("axiom: migrating %s -> %s (one-shot)", legacy, current)
    legacy.rename(current)

    # Rename all known Forven DB and key files
    renames = (
        ("forven.db", "axiom.db"),
        ("forven.db-journal", "axiom.db-journal"),
        ("forven.db-wal", "axiom.db-wal"),
        ("forven.db-shm", "axiom.db-shm"),
        ("forven_lab.db", "axiom_lab.db"),
        ("forven_lab.db-journal", "axiom_lab.db-journal"),
        ("forven_lab.db-wal", "axiom_lab.db-wal"),
        ("forven_lab.db-shm", "axiom_lab.db-shm"),
        ("forven.duckdb", "axiom.duckdb"),
        (".forven_key", ".axiom_key"),
    )
    for old_name, new_name in renames:
        old_path = current / old_name
        new_path = current / new_name
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)

    # Rename log files
    log_renames = (
        ("forven_bot.log", "axiom_bot.log"),
        ("forven_bot.err.log", "axiom_bot.err.log"),
        ("forven_daemon.log", "axiom_daemon.log"),
        ("forven_lab_worker.log", "axiom_lab_worker.log"),
    )
    logs_dir = current / "logs"
    if logs_dir.exists():
        for old_name, new_name in log_renames:
            old_path = logs_dir / old_name
            new_path = logs_dir / new_name
            if old_path.exists() and not new_path.exists():
                old_path.rename(new_path)

    try:
        (home / ".forven").mkdir(exist_ok=True)
        (home / ".forven" / BREADCRUMB).write_text(
            f"This directory's contents were moved to {current} during the "
            f"Forven -> Axiom rename. Safe to delete.\n"
        )
    except OSError:
        pass

    return True
