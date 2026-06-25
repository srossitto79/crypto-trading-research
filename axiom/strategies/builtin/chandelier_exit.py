"""Chandelier Exit strategy.

Entry: price rises above highest_high - atr_mult * ATR (uptrend start)
Exit: price falls below highest_high - atr_mult * ATR (trailing stop hit)
Compatible Regimes: TREND_UP, TREND_DOWN
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "chandelier_exit"


class ChandelierExitStrategy(BaseStrategy):
    """Chandelier Exit ATR-based trailing stop strategy.

    The chandelier exit hangs a stop-loss from the highest high of the
    lookback period at a distance of atr_mult * ATR.  Entry is signalled
    when price crosses above the chandelier line (new uptrend) and exit
    fires when price crosses below it (stop hit).
    """

    @property
    def name(self) -> str:
        return (
            f"ChandelierExit({self.params.get('atr_period', 22)}, "
            f"{self.params.get('atr_mult', 3.0)}) ({self.asset})"
        )

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "atr_period": 22,
            "atr_mult": 3.0,
            "lookback": 22,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Chandelier stop at highest_high({p['lookback']}) - "
            f"{p['atr_mult']} * ATR({p['atr_period']}). "
            f"Enters when price crosses above the stop (uptrend). "
            f"Exits when price crosses below it (stop hit)."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        atr_period = p["atr_period"]
        atr_mult = p["atr_mult"]
        lookback = p["lookback"]
        close = df["close"]
        high = df["high"]
        low = df["low"]

        min_len = max(atr_period, lookback) + 2
        if len(df) < min_len:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        # True Range
        prev_close_series = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close_series).abs(),
                (low - prev_close_series).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = tr.rolling(window=atr_period).mean()

        # Chandelier Exit (long) = Highest High - atr_mult * ATR
        highest_high = high.rolling(window=lookback).max()
        chandelier_long = highest_high - atr_mult * atr

        # Chandelier Exit (short) = Lowest Low + atr_mult * ATR
        lowest_low = low.rolling(window=lookback).min()
        chandelier_short = lowest_low + atr_mult * atr

        curr_close = float(close.iloc[-1])
        prev_close_val = float(close.iloc[-2])
        curr_chand_long = float(chandelier_long.iloc[-1])
        prev_chand_long = float(chandelier_long.iloc[-2])
        curr_chand_short = float(chandelier_short.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        curr_hh = float(highest_high.iloc[-1])

        # Entry: price crosses above the chandelier long stop from below
        entry = prev_close_val <= prev_chand_long and curr_close > curr_chand_long

        # Exit: price crosses below the chandelier long stop from above
        exit_ = prev_close_val >= prev_chand_long and curr_close < curr_chand_long

        direction = "long"

        confidence = 0.0
        if entry and curr_atr > 0:
            confidence = min(1.0, (curr_close - curr_chand_long) / curr_atr)
        elif exit_ and curr_atr > 0:
            confidence = min(1.0, (curr_chand_long - curr_close) / curr_atr)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(max(0.0, confidence), 4),
            indicators={
                "chandelier_long": round(curr_chand_long, 4),
                "chandelier_short": round(curr_chand_short, 4),
                "highest_high": round(curr_hh, 4),
                "atr": round(curr_atr, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "atr_period": (14, 28, 2),
            "atr_mult": (2.0, 4.0, 0.5),
            "lookback": (14, 28, 2),
        }


STRATEGY_CLASS = ChandelierExitStrategy

STRATEGIES = [
    ("PREBUILT-CHANDELIER", ChandelierExitStrategy, {"_asset": "BTC"}),
]
