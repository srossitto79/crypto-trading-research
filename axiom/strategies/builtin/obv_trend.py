"""OBV Trend strategy - On-Balance Volume with EMA crossover."""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "obv_trend"


class OBVTrendStrategy(BaseStrategy):
    """On-Balance Volume trend strategy.

    Entry: OBV crosses above its EMA AND close > SMA(close, sma_period).
    Exit: OBV crosses below its EMA.
    """

    @property
    def name(self) -> str:
        return f"OBV Trend {self.params.get('ema_period', 20)} ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "ema_period": 20,
            "sma_period": 20,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when OBV crosses above its {p['ema_period']}-bar EMA "
            f"and close > {p['sma_period']}-bar SMA. Exits when OBV crosses below EMA."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        volume = df["volume"]
        min_bars = max(p["ema_period"], p["sma_period"]) + 2
        if len(df) < min_bars:
            return Signal(entry_signal=False, exit_signal=False,
                          price=float(close.iloc[-1]), direction="long",
                          confidence=0.0, indicators={})

        # Compute OBV
        direction = np.sign(close.diff())
        obv = (direction * volume).cumsum()

        obv_ema = obv.ewm(span=p["ema_period"], adjust=False).mean()
        sma_close = close.rolling(window=p["sma_period"]).mean()

        curr_obv = float(obv.iloc[-1])
        prev_obv = float(obv.iloc[-2])
        curr_obv_ema = float(obv_ema.iloc[-1])
        prev_obv_ema = float(obv_ema.iloc[-2])
        curr_close = float(close.iloc[-1])
        curr_sma = float(sma_close.iloc[-1])

        cross_up = prev_obv <= prev_obv_ema and curr_obv > curr_obv_ema
        cross_down = prev_obv >= prev_obv_ema and curr_obv < curr_obv_ema
        above_sma = curr_close > curr_sma

        entry = cross_up and above_sma
        exit_ = cross_down

        confidence = 0.7 if entry else 0.0

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(confidence, 4),
            indicators={
                "obv": round(curr_obv, 2),
                "obv_ema": round(curr_obv_ema, 2),
                "sma_close": round(curr_sma, 4),
                "above_sma": bool(above_sma),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "ema_period": (10, 30, 5),
            "sma_period": (10, 30, 5),
        }


STRATEGY_CLASS = OBVTrendStrategy

STRATEGIES = [
    ("PREBUILT-OBV", OBVTrendStrategy, {"_asset": "BTC"}),
]
