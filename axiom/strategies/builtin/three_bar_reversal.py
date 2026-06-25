"""Three-Bar Reversal strategy.

Detects three consecutive lower lows followed by a higher close on the
third bar, signaling a potential reversal.  The cumulative decline must
exceed a configurable minimum percentage.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "three_bar_reversal"


class ThreeBarReversalStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Three-Bar Reversal ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return { "leverage": 1.0}

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "RANGE_BOUND"}

    def describe(self) -> str:
        md = self.params.get("min_decline_pct", 0.5)
        return (
            f"Three-bar reversal pattern requiring at least a {md}% cumulative "
            "decline across three consecutive lower lows before a higher close."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        min_decline_pct = float(self.params.get("min_decline_pct", 0.5))
        min_bars = 5
        if len(df) < min_bars:
            return Signal(price=float(df["close"].iloc[-1]))

        # Bars: -4 (anchor), -3, -2, -1 (current)
        low_4 = float(df["low"].iloc[-4])
        low_3 = float(df["low"].iloc[-3])
        low_2 = float(df["low"].iloc[-2])
        low_1 = float(df["low"].iloc[-1])

        close_2 = float(df["close"].iloc[-2])
        close_1 = float(df["close"].iloc[-1])

        # Three consecutive lower lows
        three_lower_lows = (low_3 < low_4) and (low_2 < low_3) and (low_1 < low_2)

        # Higher close on the third bar compared to the second
        higher_close = close_1 > close_2

        # Cumulative decline from bar -4 low to bar -1 low
        decline_pct = ((low_4 - low_1) / low_4) * 100.0 if low_4 > 0 else 0.0
        sufficient_decline = decline_pct >= min_decline_pct

        entry = three_lower_lows and higher_close and sufficient_decline

        # Exit when a new lower low forms after entry
        exit_ = low_1 < low_2

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(close_1, 4),
            direction="long",
            confidence=np.clip(decline_pct / (min_decline_pct * 3.0), 0.0, 1.0)
            if min_decline_pct > 0
            else 0.0,
            indicators={
                "three_lower_lows": bool(three_lower_lows),
                "higher_close": bool(higher_close),
                "decline_pct": round(decline_pct, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {"min_decline_pct": (0.2, 2.0, 0.1)}


STRATEGY_CLASS = ThreeBarReversalStrategy
STRATEGIES = [("PREBUILT-THREE-BAR", ThreeBarReversalStrategy, {"_asset": "BTC"})]
