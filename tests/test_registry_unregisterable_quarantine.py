"""2026-06-13 — a custom strategy class that fails the abstract-method contract must
be quarantined after ONE warning, not re-attempted (and re-warned) on every discover.
Overnight, 3 broken generated modules each logged ~932x. The committed fix is the
registry guard; the 3 gitignored broken files are moved aside as runtime cleanup."""
from __future__ import annotations

import logging

import pytest

from axiom.strategies import base as base_mod
from axiom.strategies import custom as custom_pkg
from axiom.strategies import registry


def _point_custom_dir(monkeypatch, tmp_path):
    d = tmp_path / "custom"
    d.mkdir()
    monkeypatch.setattr(custom_pkg, "__path__", [str(d)])
    monkeypatch.setattr(custom_pkg, "__file__", str(d / "__init__.py"))
    return d


# Missing the required generate_signal abstract method -> unregisterable.
_BROKEN = (
    "from axiom.strategies.base import BaseStrategy\n"
    "import pandas as pd\n"
    "class BrokenStrat(BaseStrategy):\n"
    "    TYPE_NAME = 'broken_quarantine_type'\n"
    "    def name(self): return 'broken'\n"
    "    def asset(self): return 'BTC/USDT'\n"
    "    def strategy_type(self): return 'broken_quarantine_type'\n"
    "    def default_params(self): return {}\n"
    "STRATEGY_CLASS = BrokenStrat\n"
    "TYPE_NAME = 'broken_quarantine_type'\n"
)

# A complete contract -> registers normally.
_COMPLETE = (
    "from axiom.strategies.base import BaseStrategy, Signal\n"
    "import pandas as pd\n"
    "class GoodStrat(BaseStrategy):\n"
    "    TYPE_NAME = 'good_quarantine_type'\n"
    "    def name(self): return 'good'\n"
    "    def asset(self): return 'BTC/USDT'\n"
    "    def strategy_type(self): return 'good_quarantine_type'\n"
    "    def default_params(self): return {}\n"
    "    def generate_signal(self, df):\n"
    "        return Signal(direction='none', entry_signal=False, exit_signal=False)\n"
    "STRATEGY_CLASS = GoodStrat\n"
    "TYPE_NAME = 'good_quarantine_type'\n"
)


class _BadCls(base_mod.BaseStrategy):
    TYPE_NAME = "unit_bad"
    def name(self):  # noqa: D401
        return "bad"
    def asset(self):
        return "BTC/USDT"
    def strategy_type(self):
        return "unit_bad"
    def default_params(self):
        return {}
    # generate_signal intentionally missing


def test_register_type_default_logs_and_skips(monkeypatch, caplog):
    monkeypatch.setattr(registry, "_TYPE_MAP", {})
    with caplog.at_level(logging.WARNING, logger="axiom.strategies.registry"):
        registry.register_type("unit_bad", _BadCls)  # no raise_on_skip
    assert "unit_bad" not in registry._TYPE_MAP
    assert any("Skipping strategy type registration" in r.message for r in caplog.records)


def test_register_type_raise_on_skip_raises():
    with pytest.raises(registry.RegistryTypeError):
        registry.register_type("unit_bad", _BadCls, raise_on_skip=True)


def test_broken_custom_module_quarantined_and_warns_once(monkeypatch, tmp_path, caplog):
    d = _point_custom_dir(monkeypatch, tmp_path)
    (d / "broken_q.py").write_text(_BROKEN, encoding="utf-8")
    registry.reset()

    with caplog.at_level(logging.WARNING, logger="axiom.strategies.registry"):
        registry.discover()
        assert "broken_quarantine_type" not in registry._TYPE_MAP
        assert "broken_q" in registry._FAILED_CUSTOM_MODULES
        assert "broken_q" in registry._FAILED_CUSTOM_LOGGED

        warns_after_first = sum(1 for r in caplog.records if "broken_q" in r.message)

        # Re-run ONLY the custom discover loop (do NOT reset — that clears the
        # failure memory). The broken module must be skipped silently this time.
        registry._custom_discovered = False
        registry._discovered = False
        registry.discover()

    warns_after_second = sum(1 for r in caplog.records if "broken_q" in r.message)
    assert warns_after_first == 1
    assert warns_after_second == 1  # no new warning on the second pass


def test_complete_custom_module_still_registers(monkeypatch, tmp_path):
    d = _point_custom_dir(monkeypatch, tmp_path)
    (d / "good_q.py").write_text(_COMPLETE, encoding="utf-8")
    registry.reset()
    registry.discover()
    assert "good_quarantine_type" in registry._TYPE_MAP
    assert "good_q" not in registry._FAILED_CUSTOM_MODULES
