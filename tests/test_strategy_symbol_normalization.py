"""Regression tests for ``axiom.db._normalize_strategy_symbol``.

Background — silent killer hunted on 2026-04-25: the symbol normalizer
accepted any string and stored it verbatim, so corrupt formats produced by
agents (timeframe baked into the pair, bare base assets) were persisted into
``strategies.symbol`` and then propagated into the OHLCV keepalive collectors,
backtest scanners, and gauntlet gates — producing 0-trade backtests forever.

Concrete observed corruption from production DB on the day of the fix:
  * ``S03013`` symbol="ETH/USDT_15M"  (timeframe baked in)
  * ``S03002`` symbol="FIL"           (no quote currency)
  * ``S01734`` symbol="BTC"           (no quote currency, paper-stage)
"""
from __future__ import annotations

import pytest

from axiom.db import _normalize_strategy_symbol, _repair_symbol_format


# --- _repair_symbol_format ------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ETH/USDT_15M", "ETH/USDT"),
    ("BTC/USDT_4H", "BTC/USDT"),
    ("DOGE/USDT_1H", "DOGE/USDT"),
    ("FIL", "FIL/USDT"),
    ("BTC", "BTC/USDT"),
    ("BTC/USDT", "BTC/USDT"),
    ("BTC-USDT", "BTC/USDT"),
    ("btc/usdt", "BTC/USDT"),
    ("SOL-USDC", "SOL/USDC"),
])
def test_repair_handles_observed_corruption(raw: str, expected: str) -> None:
    assert _repair_symbol_format(raw.upper() if raw else raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "GENERIC",
    "weird stuff!",
    "BTC/EXOTIC",
    "BTC/USDT/EXTRA",
    "BTC USDT",
    "AAPL.US",
])
def test_repair_returns_none_for_irreparable(raw: str) -> None:
    assert _repair_symbol_format(raw.upper() if raw else raw) is None


# --- _normalize_strategy_symbol ------------------------------------------

@pytest.mark.parametrize("symbol,expected", [
    ("ETH/USDT_15M", "ETH/USDT"),
    ("FIL", "FIL/USDT"),
    ("BTC", "BTC/USDT"),
    ("BTC/USDT", "BTC/USDT"),
    ("BTC-USDT", "BTC/USDT"),
])
def test_normalize_repairs_corrupt_input(symbol: str, expected: str) -> None:
    assert _normalize_strategy_symbol(symbol) == expected


def test_normalize_falls_back_to_btc_usdt_when_unrepairable() -> None:
    """Garbage input lands on the deterministic fallback, not silently
    persisted as ``GENERIC`` or ``???``."""
    assert _normalize_strategy_symbol("???!!!") == "BTC/USDT"
    assert _normalize_strategy_symbol("BTC/EXOTIC") == "BTC/USDT"


def test_normalize_consults_params_fallback_keys() -> None:
    """When the primary symbol is empty/GENERIC, params._asset/asset/symbol
    /pair are searched in priority order. Each candidate is also repaired."""
    assert _normalize_strategy_symbol("", {"_asset": "ETH"}) == "ETH/USDT"
    assert _normalize_strategy_symbol("GENERIC", {"_asset": "sol/usdt"}) == "SOL/USDT"
    assert _normalize_strategy_symbol("GENERIC", {"asset": "DOGE/USDT_4H"}) == "DOGE/USDT"
    assert _normalize_strategy_symbol(None, {"pair": "FIL"}) == "FIL/USDT"


def test_normalize_consults_params_assets_list() -> None:
    assert _normalize_strategy_symbol("", {"assets": ["BTC/USDT_15M"]}) == "BTC/USDT"
    assert _normalize_strategy_symbol("", {"assets": "ETH"}) == "ETH/USDT"


def test_normalize_skips_corrupt_candidates_then_falls_back() -> None:
    """If the primary AND every params fallback are unrepairable, we land on
    the deterministic BTC/USDT default rather than persisting garbage."""
    out = _normalize_strategy_symbol("BTC/EXOTIC", {"_asset": "weird stuff!"})
    assert out == "BTC/USDT"


def test_normalize_idempotent_on_clean_pair() -> None:
    """A clean input must round-trip without modification (no spurious upper
    /strip artifacts that would break referential equality with downstream
    matchers)."""
    assert _normalize_strategy_symbol("BTC/USDT") == "BTC/USDT"
    assert _normalize_strategy_symbol("ETH/USDT") == "ETH/USDT"
    assert _normalize_strategy_symbol("SOL/USDT") == "SOL/USDT"
