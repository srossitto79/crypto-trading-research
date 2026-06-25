"""Bullish / Bearish Engulfing pattern strategy with volume confirmation.

Detects a bullish engulfing candle (current green body completely covers
the previous red body) confirmed by above-average volume, and enters long.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "engulfing"


class EngulfingStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Engulfing Pattern ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "volume_mult": 1.5,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "RANGE_BOUND"}

    def describe(self) -> str:
        vm = self.params.get("volume_mult", 1.5)
        return (
            f"Bullish engulfing pattern strategy requiring volume >= "
            f"{vm}x the 20-bar average volume for confirmation."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        vol_mult = float(self.params.get("volume_mult", 1.5))
        vol_period = 20
        min_bars = vol_period + 2
        if len(df) < min_bars:
            return Signal(price=float(df["close"].iloc[-1]))

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        curr_open = float(curr["open"])
        curr_close = float(curr["close"])
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])

        curr_body_top = max(curr_open, curr_close)
        curr_body_bot = min(curr_open, curr_close)
        prev_body_top = max(prev_open, prev_close)
        prev_body_bot = min(prev_open, prev_close)

        # Bullish engulfing: prev red, curr green, curr body covers prev body
        prev_red = prev_close < prev_open
        curr_green = curr_close > curr_open
        body_engulfs = (curr_body_bot <= prev_body_bot) and (curr_body_top >= prev_body_top)

        bullish_engulfing = prev_red and curr_green and body_engulfs

        # Volume confirmation
        avg_volume = float(df["volume"].iloc[-vol_period - 1 : -1].mean())
        curr_volume = float(curr["volume"])
        vol_confirmed = curr_volume >= avg_volume * vol_mult if avg_volume > 0 else False

        entry = bullish_engulfing and vol_confirmed

        # Bearish engulfing for exit
        prev_green = prev_close > prev_open
        curr_red = curr_close < curr_open
        bearish_engulfs = (curr_body_bot <= prev_body_bot) and (curr_body_top >= prev_body_top)
        exit_ = prev_green and curr_red and bearish_engulfs

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=np.clip(curr_volume / (avg_volume * vol_mult), 0.0, 1.0)
            if avg_volume * vol_mult > 0
            else 0.0,
            indicators={
                "bullish_engulfing": bool(bullish_engulfing),
                "vol_confirmed": bool(vol_confirmed),
                "curr_volume": round(curr_volume, 2),
                "avg_volume": round(avg_volume, 2),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "volume_mult": (1.0, 2.5, 0.5),
        }


STRATEGY_CLASS = EngulfingStrategy
STRATEGIES = [("PREBUILT-ENGULFING", EngulfingStrategy, {"_asset": "BTC"})]
