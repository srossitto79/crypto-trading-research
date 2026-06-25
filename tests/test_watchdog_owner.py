from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def test_watchdog_owner_lock_blocks_second_owner(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    release_file = repo_root / "release.signal"
    project_root = Path(__file__).resolve().parents[1]

    holder_code = f"""
import sys
import time
from pathlib import Path

sys.path.insert(0, {str(project_root)!r})
from axiom.watchdog_owner import acquire_watchdog_owner_lock, release_watchdog_owner_lock

repo_root = Path(sys.argv[1])
release_file = Path(sys.argv[2])
assert acquire_watchdog_owner_lock(repo_root, owner_name="test-holder")
try:
    while not release_file.exists():
        time.sleep(0.1)
finally:
    release_watchdog_owner_lock(repo_root)
"""

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(repo_root), str(release_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creationflags,
    )
    try:
        deadline = time.time() + 5
        owner_file = repo_root / ".tmp" / "watchdog.owner.lock"
        while time.time() < deadline and not owner_file.exists():
            time.sleep(0.1)

        from axiom.watchdog_owner import acquire_watchdog_owner_lock, get_watchdog_owner_status

        status = get_watchdog_owner_status(repo_root)
        assert status["other_process_active"] is True
        assert status["owner_name"] == "test-holder"
        assert status["active_pid_running"] is True
        assert acquire_watchdog_owner_lock(repo_root, owner_name="second-owner") is False
    finally:
        release_file.write_text("release", encoding="utf-8")
        proc.wait(timeout=10)


def test_watchdog_owner_status_recovers_stale_owner_file(tmp_path):
    repo_root = tmp_path / "repo"
    owner_dir = repo_root / ".tmp"
    owner_dir.mkdir(parents=True)
    owner_path = owner_dir / "watchdog.owner.lock"
    owner_path.write_text(
        '{"pid": 999999, "owner_name": "stale-owner", "acquired_at": "2026-04-15T00:00:00+00:00"}',
        encoding="utf-8",
    )

    from axiom.watchdog_owner import acquire_watchdog_owner_lock, get_watchdog_owner_status, release_watchdog_owner_lock

    stale_status = get_watchdog_owner_status(repo_root)
    assert stale_status["stale_pid"] is True
    assert stale_status["active_pid_running"] is False

    assert acquire_watchdog_owner_lock(repo_root, owner_name="fresh-owner") is True
    try:
        claimed_status = get_watchdog_owner_status(repo_root)
        assert claimed_status["held_by_current_process"] is True
        assert claimed_status["owner_name"] == "fresh-owner"
        assert claimed_status["active_pid_running"] is True
    finally:
        release_watchdog_owner_lock(repo_root)


def test_launcher_scripts_use_watchdog_owner_lock():
    repo_root = Path(__file__).resolve().parents[1]
    start_all = (repo_root / "start_all.ps1").read_text(encoding="utf-8")
    watchdog = (repo_root / "watchdog.ps1").read_text(encoding="utf-8")

    assert "Acquire-WatchdogOwnerLock" in start_all
    assert "Release-WatchdogOwnerLock" in start_all
    assert "Acquire-WatchdogOwnerLock" in watchdog
    assert "Release-WatchdogOwnerLock" in watchdog
