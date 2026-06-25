"""N-bar high/low breakout strategy.

Enters when close breaks above the highest high of the last N bars.
Exits when close drops below the lowest low of the last N bars.
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "breakout_range"


class BreakoutRangeStrategy(BaseStrategy):
    """N-bar range breakout strategy."""

    @property
    def name(self) -> str:
        return f"Breakout Range ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "lookback": 20,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"BREAKOUT", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        period = int(self.params.get("lookback", 20))
        return (
            f"N-bar breakout: enters when close exceeds the {period}-bar "
            f"highest high, exits below the {period}-bar lowest low."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        period = int(self.params.get("lookback", 20))
        curr_close = float(df["close"].iloc[-1])

        if len(df) < period + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        # Highest high / lowest low over the lookback window (shift to avoid lookahead)
        highest_high = df["high"].rolling(period).max().shift(1)
        lowest_low = df["low"].rolling(period).min().shift(1)

        hh = highest_high.iloc[-1]
        ll = lowest_low.iloc[-1]

        if pd.isna(hh) or pd.isna(ll):
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        hh_val = float(hh)
        ll_val = float(ll)

        entry = curr_close > hh_val
        exit_ = curr_close < ll_val

        # Confidence based on how far beyond the breakout level
        conf = 0.0
        if entry and hh_val > 0:
            conf = min((curr_close - hh_val) / hh_val * 100, 1.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(conf, 4),
            indicators={
                "highest_high": round(hh_val, 4),
                "lowest_low": round(ll_val, 4),
                "lookback": period,
            },
        )

    def parameter_space(self) -> dict:
        return {"lookback": (10, 50, 5)}


STRATEGY_CLASS = BreakoutRangeStrategy

STRATEGIES = [
    ("PREBUILT-BREAKOUT-RANGE", BreakoutRangeStrategy, {"_asset": "BTC"}),
]
