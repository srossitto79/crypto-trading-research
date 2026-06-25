"""An active-stage strategy whose file uses an archived-style name (..._sNNNNN.py)
and whose TYPE_NAME differs from the filename must still get its runtime class
registered — otherwise it is blocked at runtime as "runtime type not registered"
after a restart (the bug that blocked the paper roster on 2026-06-15)."""

import importlib
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import axiom.strategies.custom as custom_pkg
import axiom.strategies.registry as reg
from axiom.db import get_db
from axiom.strategies.custom_catalog import custom_strategy_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_archived_named_active_strategy_is_registered(AXIOM_db):
    custom_dir = Path(custom_pkg.__file__).resolve().parent
    modname = "zz_sweeptest_s99991"          # archived-style filename (ends in _sNNNNN)
    type_name = "zz_sweeptest_type_xyz"      # TYPE_NAME intentionally differs from the filename
    src = custom_dir / f"{modname}.py"
    src.write_text(textwrap.dedent(f'''
        import pandas as pd  # noqa: F401
        from axiom.strategies.base import BaseStrategy, Signal

        TYPE_NAME = "{type_name}"

        class ZZSweepTest(BaseStrategy):
            @property
            def name(self):
                return "zz-sweeptest"
            @property
            def asset(self):
                return self.params.get("_asset", "BTC")
            @property
            def strategy_type(self):
                return TYPE_NAME
            @property
            def default_params(self):
                return {{"_asset": "BTC"}}
            def generate_signal(self, df):
                return Signal()

        STRATEGY_CLASS = ZZSweepTest
    '''), encoding="utf-8")
    importlib.invalidate_caches()
    try:
        # The archived-name filter classifies this file as "archived" (skipped by discover()).
        assert custom_strategy_status(modname) == "archived"

        # Insert an ACTIVE (paper) strategy whose `type` differs from the filename,
        # pointing at the archived-named source file.
        with get_db() as conn:
            conn.execute(
                "INSERT INTO strategies (id, name, type, symbol, timeframe, params, metrics, "
                "status, owner, stage, stage_changed_at, created_at, updated_at, source_ref) "
                "VALUES (?, ?, ?, 'BTC', '1h', '{}', '{}', 'paper', 'brain', 'paper', ?, ?, ?, ?)",
                ("S-SWEEPTEST", "zz", type_name, _now(), _now(), _now(), str(src)),
            )
            conn.commit()

        # Pre-condition: the type is NOT registered.
        reg._TYPE_MAP.pop(type_name, None)
        assert type_name not in reg._TYPE_MAP

        # The sweep registers it from source_ref despite the archived-style filename.
        reg._ensure_active_db_strategy_modules()
        assert type_name in reg._TYPE_MAP, "active archived-named strategy was not registered"
    finally:
        reg._TYPE_MAP.pop(type_name, None)
        reg._FAILED_CUSTOM_MODULES.discard(modname)
        try:
            src.unlink()
        except OSError:
            pass


def test_sweep_is_noop_when_no_source_ref(AXIOM_db):
    """A strategy row with no source_ref must not break the sweep (best-effort)."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, metrics, "
            "status, owner, stage, stage_changed_at, created_at, updated_at, source_ref) "
            "VALUES ('S-NOSRC', 'x', 'zz_nosrc_type', 'BTC', '1h', '{}', '{}', 'paper', 'brain', "
            "'paper', ?, ?, ?, NULL)",
            (_now(), _now(), _now()),
        )
        conn.commit()
    # Must not raise.
    reg._ensure_active_db_strategy_modules()
    assert "zz_nosrc_type" not in reg._TYPE_MAP
