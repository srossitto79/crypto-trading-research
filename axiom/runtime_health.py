from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import ctypes
except ImportError:  # pragma: no cover
    ctypes = None

from axiom.config import AXIOM_HOME
from axiom.db import kv_get, kv_set

_RUNTIME_FINGERPRINT_FILES = (
    "Axiom/daemon.py",
    "Axiom/control_plane/status.py",
    "Axiom/api_domains/trading.py",
    "Axiom/exchange/risk.py",
    "Axiom/exchange/hyperliquid.py",
    "Axiom/scanner.py",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_epoch_seconds(value: object) -> datetime | None:
    try:
        seconds = float(value)
    except Exception:
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except Exception:
        return None


def read_daemon_lock_pid(lock_path: Path | None = None) -> int | None:
    path = Path(lock_path or (AXIOM_HOME / "daemon.lock"))
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:
        return None


def pid_exists(pid: int) -> bool:
    normalized_pid = int(pid)
    if normalized_pid <= 0:
        return False
    if os.name == "nt" and ctypes is not None:
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information,
            False,
            normalized_pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True

    try:
        os.kill(normalized_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def remove_stale_daemon_lock(expected_pid: int | None = None) -> bool:
    lock_path = Path(AXIOM_HOME) / "daemon.lock"
    if not lock_path.exists():
        return False
    pid = read_daemon_lock_pid(lock_path)
    if expected_pid is not None and pid not in {None, int(expected_pid)}:
        return False
    if pid is not None and pid_exists(pid):
        return False
    try:
        lock_path.unlink()
    except OSError:
        return False
    return True


def normalize_daemon_state(
    *,
    stale_after_seconds: float = 900.0,
    write_back: bool = True,
) -> dict[str, Any]:
    raw_state = kv_get("daemon_state", {}) or {}
    state = dict(raw_state) if isinstance(raw_state, dict) else {}

    pid = state.get("pid")
    try:
        normalized_pid = int(pid) if pid is not None else None
    except Exception:
        normalized_pid = None
    if normalized_pid is None:
        normalized_pid = read_daemon_lock_pid()

    process_alive = pid_exists(normalized_pid) if normalized_pid is not None else None
    last_tick = _parse_epoch_seconds(state.get("last_tick_ts")) or _parse_timestamp(state.get("last_scan"))
    age_seconds = None
    if last_tick is not None:
        age_seconds = (datetime.now(timezone.utc) - last_tick).total_seconds()

    running = bool(state.get("running"))
    stale_process = bool(
        running
        and process_alive is False
        and (age_seconds is None or age_seconds > max(float(stale_after_seconds), 1.0))
    )
    if stale_process:
        state["running"] = False
        state["stopped_at"] = state.get("stopped_at") or _now_iso()
        state["stale_process_detected"] = True
        state["stale_pid"] = normalized_pid
        remove_stale_daemon_lock(normalized_pid)
        if write_back:
            kv_set("daemon_state", state)

    derived = dict(state)
    derived["pid"] = normalized_pid
    derived["process_alive"] = process_alive
    derived["age_seconds"] = None if age_seconds is None else round(age_seconds, 3)
    return derived


def compute_runtime_code_fingerprint(paths: tuple[str, ...] | None = None) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    selected_paths = tuple(paths or _RUNTIME_FINGERPRINT_FILES)
    digest = hashlib.sha256()
    included_files: list[str] = []

    for rel_path in selected_paths:
        path = repo_root / rel_path
        if not path.exists() or not path.is_file():
            continue
        included_files.append(rel_path)
        digest.update(rel_path.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except Exception:
            digest.update(str(path.stat().st_mtime_ns).encode("utf-8"))

    return {
        "fingerprint": digest.hexdigest(),
        "files": included_files,
        "generated_at": _now_iso(),
    }
