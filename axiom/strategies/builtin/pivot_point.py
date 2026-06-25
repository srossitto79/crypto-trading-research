"""Pivot Point Support/Resistance Bounce strategy.

Calculates classic pivot points (Pivot, S1, R1) from the previous bar's
high, low, and close.  Enters long when price bounces off S1 and exits
at R1.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "pivot_point"


class PivotPointStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Pivot Point S/R Bounce ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {"lookback": 1, "leverage": 1.0}

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}

    def describe(self) -> str:
        lb = self.params.get("lookback", 1)
        return (
            f"Classic pivot point strategy using a {lb}-bar lookback. "
            "Enters long on a bounce off S1 and targets R1 for exit."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        lookback = int(self.params.get("lookback", 1))
        min_bars = lookback + 2
        if len(df) < min_bars:
            return Signal(price=float(df["close"].iloc[-1]))

        prev = df.iloc[-(lookback + 1)]
        curr_close = float(df["close"].iloc[-1])
        curr_low = float(df["low"].iloc[-1])

        pivot = (float(prev["high"]) + float(prev["low"]) + float(prev["close"])) / 3.0
        s1 = 2.0 * pivot - float(prev["high"])
        r1 = 2.0 * pivot - float(prev["low"])

        # Entry: price touched or dipped below S1 then closed above it
        bounce_off_s1 = (curr_low <= s1) and (curr_close > s1)
        # Exit: price reached R1
        hit_r1 = curr_close >= r1

        return Signal(
            entry_signal=bool(bounce_off_s1),
            exit_signal=bool(hit_r1),
            price=round(curr_close, 4),
            direction="long",
            confidence=np.clip((r1 - s1) / curr_close, 0.0, 1.0) if curr_close > 0 else 0.0,
            indicators={
                "pivot": round(pivot, 4),
                "s1": round(s1, 4),
                "r1": round(r1, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {"lookback": (1, 5, 1)}


STRATEGY_CLASS = PivotPointStrategy
STRATEGIES = [("PREBUILT-PIVOT", PivotPointStrategy, {"_asset": "BTC"})]
