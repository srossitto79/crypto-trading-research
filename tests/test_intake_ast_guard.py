"""Lead-2 (part A): every custom-code ingress path must run the AST guard before
importing the module in-process. The agent path previously imported on a ruff
pass alone — asymmetric with the manual authoring path which scans. The guard
now lives in intake.register_custom_strategy_file, the shared chokepoint.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from axiom.strategies import custom as custom_pkg
from axiom.strategies import intake as intake_mod
from axiom.strategies import registry

_CLEAN = """\
import pandas as pd
from axiom.strategies.base import BaseStrategy, Signal


class GuardOkStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return 'Guard OK'

    @property
    def asset(self) -> str:
        return 'BTC'

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {'risk_pct': 0.01, 'leverage': 1.0}

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        price = float(df['close'].iloc[-1]) if 'close' in df and len(df.index) else 0.0
        return Signal(price=price)


STRATEGY_CLASS = GuardOkStrategy
TYPE_NAME = 'guard_ok_test'
"""

# Same clean body, but with an exfiltration primitive injected at top level.
_MALICIOUS = _CLEAN.replace(
    "import pandas as pd\n",
    "import pandas as pd\nimport os\nimport socket\n",
).replace("guard_ok_test", "guard_evil_test").replace(
    "GuardOkStrategy", "GuardEvilStrategy"
).replace("'Guard OK'", "'Guard Evil'")


def _point_custom_dir(monkeypatch, tmp_path):
    d = tmp_path / "custom"
    d.mkdir()
    monkeypatch.setattr(custom_pkg, "__path__", [str(d)])
    monkeypatch.setattr(custom_pkg, "__file__", str(d / "__init__.py"))
    registry.reset()
    importlib.invalidate_caches()
    return d


def test_malicious_top_level_import_is_rejected_before_import(AXIOM_db, monkeypatch, tmp_path):
    d = _point_custom_dir(monkeypatch, tmp_path)
    f = d / "btc_guard_evil_test.py"
    f.write_text(_MALICIOUS, encoding="utf-8")
    sys.modules.pop("axiom.strategies.custom.btc_guard_evil_test", None)

    with pytest.raises(ValueError) as exc:
        intake_mod.register_custom_strategy_file(file_path=str(f))
    msg = str(exc.value).lower()
    assert "security scan" in msg
    # the module must NOT have been imported in-process
    assert "axiom.strategies.custom.btc_guard_evil_test" not in sys.modules


def test_clean_strategy_still_registers(AXIOM_db, monkeypatch, tmp_path):
    d = _point_custom_dir(monkeypatch, tmp_path)
    f = d / "btc_guard_ok_test.py"
    f.write_text(_CLEAN, encoding="utf-8")
    sys.modules.pop("axiom.strategies.custom.btc_guard_ok_test", None)

    result = intake_mod.register_custom_strategy_file(file_path=str(f))
    assert result["strategy_id"]
    assert result["module_name"] == "btc_guard_ok_test"
