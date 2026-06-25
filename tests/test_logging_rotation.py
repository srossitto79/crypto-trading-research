"""H-Op1: rotating file log handler prevents unbounded log growth."""

from __future__ import annotations

import logging
import logging.handlers

import pytest

from axiom.logging_config import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    _env_int,
    setup_rotating_file_logger,
)


@pytest.fixture
def reset_root_logger():
    """Snapshot and restore the root logger around each test so the
    helper's force=True doesn't leak into other test modules."""
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    saved_filters = list(root.filters)
    yield
    root.handlers = saved_handlers
    root.filters = saved_filters
    root.setLevel(saved_level)


def test_defaults_are_50mib_and_3_backups():
    assert DEFAULT_MAX_BYTES == 50 * 1024 * 1024
    assert DEFAULT_BACKUP_COUNT == 3


def test_env_int_parses_positive_ints(monkeypatch):
    monkeypatch.setenv("AXIOM_LOG_MAX_BYTES", "1048576")
    assert _env_int("AXIOM_LOG_MAX_BYTES", 999) == 1048576


def test_env_int_falls_back_on_empty(monkeypatch):
    monkeypatch.delenv("AXIOM_LOG_MAX_BYTES", raising=False)
    assert _env_int("AXIOM_LOG_MAX_BYTES", 123) == 123


def test_env_int_falls_back_on_invalid(monkeypatch):
    monkeypatch.setenv("AXIOM_LOG_MAX_BYTES", "not-a-number")
    assert _env_int("AXIOM_LOG_MAX_BYTES", 999) == 999


def test_env_int_rejects_zero_and_negative(monkeypatch):
    monkeypatch.setenv("AXIOM_LOG_MAX_BYTES", "0")
    assert _env_int("AXIOM_LOG_MAX_BYTES", 999) == 999
    monkeypatch.setenv("AXIOM_LOG_MAX_BYTES", "-5")
    assert _env_int("AXIOM_LOG_MAX_BYTES", 999) == 999


def test_setup_installs_rotating_handler(tmp_path, reset_root_logger):
    log_path = tmp_path / "test.log"
    setup_rotating_file_logger(log_path, level=logging.INFO)

    rotating = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == DEFAULT_MAX_BYTES
    assert rotating[0].backupCount == DEFAULT_BACKUP_COUNT


def test_setup_rotates_when_threshold_exceeded(tmp_path, reset_root_logger):
    """Write enough to trigger one rotation and verify .1 appears."""
    log_path = tmp_path / "rotate.log"
    # Set a tiny threshold so a few log lines trigger rotation.
    setup_rotating_file_logger(
        log_path, level=logging.INFO, max_bytes=500, backup_count=2,
    )
    logger = logging.getLogger("axiom.test_rotation")
    for i in range(50):
        logger.info("padding-message-number-%d-%s", i, "X" * 50)

    # Flush/force rollover by closing handlers.
    for h in logging.getLogger().handlers:
        try: h.close()
        except Exception: pass

    assert log_path.exists(), "primary log file not created"
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists(), "expected at least one rotated backup (.1)"


def test_setup_creates_parent_directory(tmp_path, reset_root_logger):
    log_path = tmp_path / "deep" / "nested" / "dir" / "app.log"
    setup_rotating_file_logger(log_path)
    assert log_path.parent.exists()


def test_setup_env_override_for_max_bytes(tmp_path, reset_root_logger, monkeypatch):
    monkeypatch.setenv("AXIOM_LOG_MAX_BYTES", "12345")
    log_path = tmp_path / "envy.log"
    setup_rotating_file_logger(log_path)
    rotating = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert rotating and rotating[0].maxBytes == 12345
