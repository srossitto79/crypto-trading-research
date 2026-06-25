"""Composite strategy base classes.

Each base class pre-wires a common two-signal pattern so the agent
generates working code by filling in one abstract method rather than
writing all boilerplate from scratch.
"""

from axiom.strategies.composite.trend_filter import TrendFilterStrategy
from axiom.strategies.composite.momentum_confirmation import MomentumConfirmationStrategy
from axiom.strategies.composite.mean_reversion_gate import MeanReversionGateStrategy
from axiom.strategies.composite.funding_regime import FundingRegimeStrategy

__all__ = [
    "TrendFilterStrategy",
    "MomentumConfirmationStrategy",
    "MeanReversionGateStrategy",
    "FundingRegimeStrategy",
]
