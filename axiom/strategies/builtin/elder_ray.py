"""Elder Ray (Bull / Bear Power) strategy.

Uses an EMA of close prices to derive Bull Power (High - EMA) and
Bear Power (Low - EMA).  Enters long when the EMA is rising and
Bull Power crosses above zero.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "elder_ray"


class ElderRayStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Elder Ray ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {"ema_period": 13, "leverage": 1.0}

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP"}

    def describe(self) -> str:
        ep = self.params.get("ema_period", 13)
        return (
            f"Elder Ray strategy with a {ep}-period EMA. "
            "Enters long when EMA is rising and Bull Power crosses above zero."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_period = int(self.params.get("ema_period", 13))
        min_bars = ema_period + 2
        if len(df) < min_bars:
            return Signal(price=float(df["close"].iloc[-1]))

        ema = df["close"].ewm(span=ema_period, adjust=False).mean()

        bull_power = df["high"] - ema
        bear_power = df["low"] - ema

        curr_bull = float(bull_power.iloc[-1])
        prev_bull = float(bull_power.iloc[-2])
        ema_rising = float(ema.iloc[-1]) > float(ema.iloc[-2])

        # Entry: EMA rising and Bull Power crosses above 0
        entry = ema_rising and (prev_bull <= 0.0) and (curr_bull > 0.0)

        # Exit: Bull Power falls back below 0 or EMA declining
        exit_ = (curr_bull < 0.0) or (not ema_rising)

        curr_close = float(df["close"].iloc[-1])
        curr_bear = float(bear_power.iloc[-1])

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=np.clip(curr_bull / curr_close, 0.0, 1.0)
            if curr_close > 0
            else 0.0,
            indicators={
                "bull_power": round(curr_bull, 4),
                "bear_power": round(curr_bear, 4),
                "ema": round(float(ema.iloc[-1]), 4),
                "ema_rising": ema_rising,
            },
        )

    def parameter_space(self) -> dict:
        return {"ema_period": (5, 26, 1)}


STRATEGY_CLASS = ElderRayStrategy
STRATEGIES = [("PREBUILT-ELDER-RAY", ElderRayStrategy, {"_asset": "BTC"})]
