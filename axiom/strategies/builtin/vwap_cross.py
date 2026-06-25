"""VWAP Cross strategy."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "vwap_cross"


class VWAPCrossStrategy(BaseStrategy):
    """VWAP crossover strategy using rolling VWAP.

    Entry: Close crosses above VWAP from below.
    Exit: Close crosses below VWAP from above.
    """

    @property
    def name(self) -> str:
        return f"VWAP Cross {self.params.get('vwap_period', 20)} ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "vwap_period": 20,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "RANGE_BOUND"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when close crosses above rolling {p['vwap_period']}-bar VWAP. "
            f"Exits when close crosses below VWAP."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        high = df["high"]
        low = df["low"]
        close = df["close"]
        volume = df["volume"]
        period = p["vwap_period"]

        if len(df) < period + 2:
            return Signal(entry_signal=False, exit_signal=False,
                          price=float(close.iloc[-1]), direction="long",
                          confidence=0.0, indicators={})

        typical_price = (high + low + close) / 3.0
        tp_vol = typical_price * volume
        # Rolling VWAP
        vwap = tp_vol.rolling(window=period).sum() / volume.rolling(window=period).sum().replace(0, 1e-10)

        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        curr_vwap = float(vwap.iloc[-1])
        prev_vwap = float(vwap.iloc[-2])

        cross_above = prev_close <= prev_vwap and curr_close > curr_vwap
        cross_below = prev_close >= prev_vwap and curr_close < curr_vwap

        entry = cross_above
        exit_ = cross_below

        confidence = 0.6 if entry else 0.0

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(confidence, 4),
            indicators={
                "vwap": round(curr_vwap, 4),
                "close": round(curr_close, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "vwap_period": (10, 40, 5),
        }


STRATEGY_CLASS = VWAPCrossStrategy

STRATEGIES = [
    ("PREBUILT-VWAP-CROSS", VWAPCrossStrategy, {"_asset": "BTC"}),
]
