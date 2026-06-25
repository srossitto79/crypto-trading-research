"""One-shot cutover: merge ~/.juddex into ~/.Axiom and (optionally) delete ~/.juddex.

Background: the pre-rename home was `~/.juddex/` and used DB files named
`juddex.db` / `juddex_lab.db`. The canonical home is `~/.Axiom/` with
`axiom.db` / `axiom_lab.db`. The automatic migration in `Axiom.config`
refuses to overwrite files that already exist in the canonical home, so a
freshly-initialized empty `axiom.db` blocks the real data from being picked
up.

This script does the cutover explicitly:

  1. Copies DB files with renames (juddex.db -> axiom.db, juddex_lab.db ->
     axiom_lab.db). If the canonical DB already exists it is backed up as
     `<name>.pre-juddex-migration.bak` before being replaced. Replacement
     only happens when the legacy DB has at least as many strategies as the
     canonical one (never silently discards newer data).
  2. Merges every other file/dir from `~/.juddex/` into `~/.Axiom/`,
     preferring existing canonical files (does not overwrite). This picks up
     anything the app still expects (custom strategies, workspace, chroma,
     config) without clobbering newer state in the canonical home.
  3. With `--cleanup`, removes `~/.juddex/` after a successful merge.
     Without it, `~/.juddex/` is left intact so you can verify first.

Usage:
    # 0. Stop the Axiom backend (it holds axiom.db open).
    python scripts/migrate_juddex_home.py            # dry-safe: no delete
    python scripts/migrate_juddex_home.py --cleanup  # also removes ~/.juddex
    # 1. Restart the backend.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

HOME = Path.home()
LEGACY = HOME / ".juddex"
CANONICAL = HOME / ".Axiom"

DB_RENAMES = {
    "juddex.db": "axiom.db",
    "juddex.db-journal": "axiom.db-journal",
    "juddex.db-wal": "axiom.db-wal",
    "juddex.db-shm": "axiom.db-shm",
    "juddex_lab.db": "axiom_lab.db",
    "juddex_lab.db-journal": "axiom_lab.db-journal",
    "juddex_lab.db-wal": "axiom_lab.db-wal",
    "juddex_lab.db-shm": "axiom_lab.db-shm",
}

# Only back-up-and-overwrite these (the main DBs). Sidecar WAL/SHM/journal
# files are migrated via the same rename map, but only if the primary DB is
# being replaced — otherwise they'd mismatch the in-place primary DB.
PRIMARY_DB_PAIRS = [
    ("juddex.db", "axiom.db"),
    ("juddex_lab.db", "axiom_lab.db"),
]


def _strategy_count(db_path: Path) -> int | str:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
    except sqlite3.OperationalError as exc:
        return f"open-err: {exc}"
    try:
        try:
            cur = conn.execute("SELECT COUNT(*) FROM strategies")
            return int(cur.fetchone()[0])
        except sqlite3.OperationalError:
            return "no-strategies-table"
    finally:
        conn.close()


def _backup_and_replace(src: Path, dst: Path) -> None:
    if dst.exists():
        backup = dst.with_suffix(dst.suffix + ".pre-juddex-migration.bak")
        if backup.exists():
            backup.unlink()
        dst.rename(backup)
        print(f"  backed up {dst.name} -> {backup.name}")
    shutil.copy2(src, dst)
    print(f"  copied {src.name} -> {dst.name} ({dst.stat().st_size} bytes)")


def _migrate_db_pair(legacy_name: str, canonical_name: str, replaced: list[str]) -> bool:
    """Return True if the canonical DB was replaced from the legacy copy."""
    src = LEGACY / legacy_name
    dst = CANONICAL / canonical_name
    if not src.exists() or src.stat().st_size == 0:
        print(f"skip {legacy_name}: legacy missing or empty")
        return False

    src_count = _strategy_count(src)
    dst_count = _strategy_count(dst)
    if isinstance(src_count, int) and isinstance(dst_count, int) and dst_count >= src_count and dst_count > 0:
        print(f"skip {legacy_name}: canonical {canonical_name} has >= strategies ({dst_count} >= {src_count})")
        return False

    print(f"migrate {legacy_name} (strategies={src_count}) -> {canonical_name} (was strategies={dst_count})")
    _backup_and_replace(src, dst)
    replaced.append(canonical_name)

    # Bring sidecar files along only when primary DB was replaced.
    for suffix in ("-journal", "-wal", "-shm"):
        sc = LEGACY / (legacy_name + suffix)
        dc = CANONICAL / (canonical_name + suffix)
        if sc.exists():
            if dc.exists():
                dc.unlink()
            shutil.copy2(sc, dc)
            print(f"  copied sidecar {sc.name} -> {dc.name}")
    return True


def _merge_nonconflicting(source_root: Path, dest_root: Path) -> tuple[int, int]:
    """Copy files/dirs from source to dest if dest doesn't already have them.

    DB files and their sidecars are skipped here (handled separately).
    Returns (files_copied, dirs_copied).
    """
    files_copied = 0
    dirs_copied = 0
    for src in sorted(source_root.iterdir()):
        if src.name in DB_RENAMES:
            continue  # handled by the DB migration path
        dest_name = src.name
        dst = dest_root / dest_name
        if src.is_dir():
            if not dst.exists():
                shutil.copytree(src, dst)
                dirs_copied += 1
                print(f"  merged dir {src.name}/")
            else:
                sub_f, sub_d = _merge_nonconflicting(src, dst)
                files_copied += sub_f
                dirs_copied += sub_d
            continue
        if not dst.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                files_copied += 1
                print(f"  merged file {src.name}")
            except Exception as exc:
                print(f"  SKIP {src.name}: {exc}")
    return files_copied, dirs_copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cleanup", action="store_true", help="Remove ~/.juddex after a successful merge.")
    args = parser.parse_args()

    if not LEGACY.exists():
        print(f"legacy home not found: {LEGACY}  (nothing to do)")
        return 0

    CANONICAL.mkdir(parents=True, exist_ok=True)

    print("== pre-migration ==")
    for _, canonical_name in PRIMARY_DB_PAIRS:
        dst = CANONICAL / canonical_name
        print(f"  {dst}: strategies={_strategy_count(dst)}")
    for legacy_name, _ in PRIMARY_DB_PAIRS:
        src = LEGACY / legacy_name
        print(f"  {src}: strategies={_strategy_count(src)}")

    print("\n== step 1: DB migration ==")
    replaced: list[str] = []
    for legacy_name, canonical_name in PRIMARY_DB_PAIRS:
        _migrate_db_pair(legacy_name, canonical_name, replaced)

    print("\n== step 2: merge non-DB files (non-destructive) ==")
    files_copied, dirs_copied = _merge_nonconflicting(LEGACY, CANONICAL)
    print(f"  merged {files_copied} files, {dirs_copied} new directories")

    print("\n== post-migration ==")
    for _, canonical_name in PRIMARY_DB_PAIRS:
        dst = CANONICAL / canonical_name
        print(f"  {dst}: strategies={_strategy_count(dst)}")

    if args.cleanup:
        if not replaced:
            print("\n--cleanup requested but no DB was replaced; refusing to delete ~/.juddex.")
            print("  (Run without --cleanup first, or delete manually if you've verified the state.)")
            return 0
        print("\n== step 3: removing legacy home ==")
        try:
            shutil.rmtree(LEGACY)
            print(f"  removed {LEGACY}")
        except Exception as exc:
            print(f"  FAILED to remove {LEGACY}: {exc}")
            return 2
    else:
        print(f"\n{LEGACY} left in place. Re-run with --cleanup to remove it after verification.")

    print("\nDone. Restart the Axiom backend, then reload The Forge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
