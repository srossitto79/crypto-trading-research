"""Shared fixtures for Axiom/Axiom tests."""

import asyncio
import os
import tempfile
from unittest.mock import patch

# Enable feature-flagged modules for testing (must be set before Axiom.api import)
os.environ.setdefault("AXIOM_ENABLE_REGIME_LAB", "1")

# COLLECTION-TIME isolation (audit M-5): the per-test fixture below patches
# AXIOM_HOME only while a test runs, but pytest collection imports every test
# module first — and module-level imports (e.g. Axiom.ai's model-routing
# snapshot) open the DB at import time. Without this, collecting the suite on
# an operator machine connects to (and can create) the LIVE ~/.Axiom/axiom.db
# while the app is trading. Point AXIOM_HOME at a throwaway dir before any
# Axiom import; an explicit operator-provided AXIOM_HOME is respected.
if not os.environ.get("AXIOM_HOME"):
    os.environ["AXIOM_HOME"] = tempfile.mkdtemp(prefix="axiom_pytest_home_")

# Captured before any Axiom.api import: importing that module on Windows swaps
# the global asyncio policy to the Selector loop (for API socket stability),
# which cannot spawn subprocesses. Hold the process default so we can restore it
# after each test and keep the side effect from leaking into subprocess-based
# asyncio tests (e.g. the MCP client) that run later in the session.
_DEFAULT_EVENT_LOOP_POLICY = asyncio.get_event_loop_policy()

import pytest


@pytest.fixture(autouse=True)
def _restore_event_loop_policy():
    yield
    asyncio.set_event_loop_policy(_DEFAULT_EVENT_LOOP_POLICY)


@pytest.fixture(autouse=True)
def _isolate_AXIOM_home(tmp_path):
    """Point AXIOM_HOME to a temp dir so tests don't touch ~/.Axiom.

    Patches both config module AND db module references so get_db()
    connects to the temp DB, not the production one.
    """
    home = tmp_path / ".Axiom"
    home.mkdir()
    (home / "data").mkdir()
    (home / "workspace").mkdir()
    (home / "workspace" / "memory").mkdir()
    (home / "workspace" / "agents").mkdir()

    db_path = home / "axiom.db"
    lab_db_path = home / "axiom_lab.db"

    import axiom.config as cfg
    import axiom.db as db_mod

    orig_home = cfg.AXIOM_HOME
    orig_cfg_db = cfg.AXIOM_DB
    orig_lab_db = cfg.AXIOM_LAB_DB
    orig_config = cfg.CONFIG_FILE
    orig_data = getattr(cfg, "DATA_DIR", None)
    orig_workspace = getattr(cfg, "WORKSPACE_DIR", None)
    orig_db_ref = db_mod.AXIOM_DB

    with patch.dict(os.environ, {"AXIOM_HOME": str(home)}):
        cfg.AXIOM_HOME = home
        cfg.AXIOM_DB = db_path
        cfg.AXIOM_LAB_DB = lab_db_path
        cfg.CONFIG_FILE = home / "config.json"
        cfg.DATA_DIR = home / "data"
        cfg.WORKSPACE_DIR = home / "workspace"
        # Critical: patch the db module's own reference too
        db_mod.AXIOM_DB = db_path

        yield home

        cfg.AXIOM_HOME = orig_home
        cfg.AXIOM_DB = orig_cfg_db
        cfg.AXIOM_LAB_DB = orig_lab_db
        cfg.CONFIG_FILE = orig_config
        if orig_data is not None:
            cfg.DATA_DIR = orig_data
        if orig_workspace is not None:
            cfg.WORKSPACE_DIR = orig_workspace
        db_mod.AXIOM_DB = orig_db_ref


@pytest.fixture
def AXIOM_db(tmp_path):
    """Initialize the isolated Axiom SQLite DB with schema."""
    import axiom.config as cfg
    import axiom.db as db_mod

    db_path = cfg.AXIOM_DB
    # Ensure db module also points here (should already via _isolate_AXIOM_home)
    db_mod.AXIOM_DB = db_path

    from axiom.db import init_db
    init_db()
    return db_path
