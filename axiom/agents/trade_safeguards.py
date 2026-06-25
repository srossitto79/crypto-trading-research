"""Execution-trader trade safeguards — regime/direction sanity checks before an order.

This module is the in-package home of the safeguards that the ``place_order`` agent
tool runs before submitting an order. It was previously imported from a hard-coded,
machine-specific path (``/home/trestor/.Axiom/workspace/agents/execution-trader``),
which raised ``ModuleNotFoundError`` on every other machine and silently disabled the
safeguard layer. It now lives in ``axiom.agents`` and is imported normally.

The single behavioural rule today is conservative and intentionally narrow so it
blocks the clearly-wrong case (opening a LONG into a confirmed downtrend) without
over-blocking the legitimate ones. Regime gating for *which strategies may trade in
which regime* is handled upstream in ``Axiom.regime``; this is a last-line defence on
the execution-trader's own order path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from axiom.regime import (
    HIGH_VOL,
    RANGE_BOUND,
    TREND_DOWN,
    TREND_UP,
    normalize_regime_label,
)

# Canonical-label -> enum-value map. Enum values are the lowercase forms the agent
# tool tends to read out of ``signal_data['regime']`` (default "range_bound"), but
# construction is normalized via ``_missing_`` so any casing/alias resolves.
_CANONICAL_TO_VALUE = {
    RANGE_BOUND: "range_bound",
    TREND_UP: "trend_up",
    TREND_DOWN: "trend_down",
    HIGH_VOL: "high_vol",
}


class MarketRegime(str, Enum):
    """Market regime usable as ``MarketRegime(some_string)`` from any alias/casing.

    Accepts canonical labels (``RANGE_BOUND``), lowercase (``range_bound``), and the
    aliases understood by :func:`Axiom.regime.normalize_regime_label`
    (e.g. ``trending_down`` -> ``TREND_DOWN``, ``volatile`` -> ``HIGH_VOL``).
    Unrecognized values raise ``ValueError`` so the caller's existing
    ``except ValueError`` fallback to ``RANGE_BOUND`` still applies.
    """

    RANGE_BOUND = "range_bound"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    HIGH_VOL = "high_vol"

    @classmethod
    def _missing_(cls, value: object) -> "MarketRegime | None":
        canonical = normalize_regime_label(value)
        if canonical is None:
            return None
        mapped = _CANONICAL_TO_VALUE.get(canonical)
        if mapped is None:
            return None
        return cls(mapped)


@dataclass(frozen=True)
class SafeguardResult:
    """Outcome of a safeguard check. ``passed`` False means block the trade."""

    passed: bool
    message: str = ""


class TradeSafeguards:
    """Pre-trade safeguards for the execution-trader agent."""

    # Regimes in which opening a NEW long is treated as unsafe (catching a falling
    # knife). Kept deliberately narrow: only a confirmed downtrend blocks longs.
    _LONG_BLOCKED_REGIMES = frozenset({MarketRegime.TREND_DOWN})

    def check_regime_for_long(self, regime: MarketRegime) -> SafeguardResult:
        """Block opening a LONG in a confirmed downtrend; allow otherwise."""
        if regime in self._LONG_BLOCKED_REGIMES:
            return SafeguardResult(
                passed=False,
                message=(
                    f"Refusing to open LONG in {regime.value} regime "
                    "(confirmed downtrend). Wait for the regime to stabilize."
                ),
            )
        return SafeguardResult(passed=True, message=f"Regime {regime.value} OK for long")

    def check_regime_for_short(self, regime: MarketRegime) -> SafeguardResult:
        """Symmetric guard: block opening a SHORT in a confirmed uptrend."""
        if regime == MarketRegime.TREND_UP:
            return SafeguardResult(
                passed=False,
                message=(
                    f"Refusing to open SHORT in {regime.value} regime "
                    "(confirmed uptrend). Wait for the regime to stabilize."
                ),
            )
        return SafeguardResult(passed=True, message=f"Regime {regime.value} OK for short")


__all__ = ["MarketRegime", "SafeguardResult", "TradeSafeguards"]
