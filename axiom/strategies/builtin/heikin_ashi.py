"""Heikin-Ashi candle color-change trend-entry strategy.

Computes Heikin-Ashi candles (HA_close, HA_open) and enters when the
candle color flips from red to green for a configurable number of
confirmation bars.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "heikin_ashi"


class HeikinAshiStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Heikin-Ashi Color Change ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {"confirmation_bars": 2, "leverage": 1.0}

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        cb = self.params.get("confirmation_bars", 2)
        return (
            f"Heikin-Ashi color-change strategy requiring {cb} consecutive "
            "green bars after a red-to-green flip to confirm trend entry."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        confirmation_bars = int(self.params.get("confirmation_bars", 2))
        min_bars = confirmation_bars + 3
        if len(df) < min_bars:
            return Signal(price=float(df["close"].iloc[-1]))

        # Build Heikin-Ashi series
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        ha_open = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
        ha_open.iloc[0] = (float(df["open"].iloc[0]) + float(df["close"].iloc[0])) / 2.0
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

        # Green = HA_close > HA_open, Red = otherwise
        green = ha_close > ha_open

        # Check for confirmation_bars consecutive green bars ending at current bar
        all_green = all(bool(green.iloc[-(j + 1)]) for j in range(confirmation_bars))
        # The bar before the run must be red (color change)
        was_red = not bool(green.iloc[-(confirmation_bars + 1)])

        entry = was_red and all_green

        # Exit on color change back to red
        exit_ = not bool(green.iloc[-1])

        curr_close = float(df["close"].iloc[-1])
        curr_ha_close = float(ha_close.iloc[-1])
        curr_ha_open = float(ha_open.iloc[-1])

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=np.clip(abs(curr_ha_close - curr_ha_open) / curr_close, 0.0, 1.0)
            if curr_close > 0
            else 0.0,
            indicators={
                "ha_close": round(curr_ha_close, 4),
                "ha_open": round(curr_ha_open, 4),
                "ha_green": bool(green.iloc[-1]),
            },
        )

    def parameter_space(self) -> dict:
        return {"confirmation_bars": (1, 5, 1)}


STRATEGY_CLASS = HeikinAshiStrategy
STRATEGIES = [("PREBUILT-HEIKIN-ASHI", HeikinAshiStrategy, {"_asset": "BTC"})]
