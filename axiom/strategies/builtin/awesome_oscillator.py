"""Awesome Oscillator strategy.

AO = SMA(median_price, 5) - SMA(median_price, 34)
Entry: AO crosses above 0
Exit: AO crosses below 0
Compatible Regimes: TREND_UP, TREND_DOWN
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "awesome_oscillator"


class AwesomeOscillatorStrategy(BaseStrategy):
    """Awesome Oscillator momentum strategy.

    AO = SMA(median_price, fast) - SMA(median_price, slow)
    where median_price = (high + low) / 2.
    Entry on zero-line cross up; exit on zero-line cross down.
    """

    @property
    def name(self) -> str:
        p = self.params
        return f"AO({p.get('fast_period', 5)}/{p.get('slow_period', 34)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "fast_period": 5,
            "slow_period": 34,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Awesome Oscillator({p['fast_period']}/{p['slow_period']}). "
            f"Enters when AO crosses above 0; exits when AO crosses below 0."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        slow = p["slow_period"]

        if len(df) < slow + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        median_price = (df["high"] + df["low"]) / 2.0
        sma_fast = median_price.rolling(window=p["fast_period"]).mean()
        sma_slow = median_price.rolling(window=slow).mean()
        ao = sma_fast - sma_slow

        curr_ao = float(ao.iloc[-1])
        prev_ao = float(ao.iloc[-2])
        curr_close = float(close.iloc[-1])

        entry = prev_ao <= 0 and curr_ao > 0
        exit_ = prev_ao >= 0 and curr_ao < 0

        confidence = 0.0
        if entry:
            confidence = min(1.0, abs(curr_ao) / (curr_close * 0.005 + 1e-10))

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "ao": round(curr_ao, 4),
                "prev_ao": round(prev_ao, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "fast_period": (3, 7, 1),
            "slow_period": (28, 40, 2),
        }


STRATEGY_CLASS = AwesomeOscillatorStrategy

STRATEGIES = [
    ("PREBUILT-AO", AwesomeOscillatorStrategy, {"_asset": "BTC"}),
]
