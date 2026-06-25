"""Accumulation/Distribution Line strategy."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "adl"


class ADLStrategy(BaseStrategy):
    """Accumulation/Distribution Line strategy.

    Entry: ADL crosses above its EMA AND close is trending up.
    Exit: ADL crosses below its EMA.
    """

    @property
    def name(self) -> str:
        return f"ADL {self.params.get('ema_period', 20)} ({self.asset})"

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
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when A/D line crosses above its {p['ema_period']}-bar EMA "
            f"and close is trending up. Exits when ADL crosses below EMA."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        high = df["high"]
        low = df["low"]
        close = df["close"]
        volume = df["volume"]

        if len(df) < p["ema_period"] + 2:
            return Signal(entry_signal=False, exit_signal=False,
                          price=float(close.iloc[-1]), direction="long",
                          confidence=0.0, indicators={})

        # A/D = cumsum of ((close - low) - (high - close)) / (high - low) * volume
        hl_range = high - low
        hl_range = hl_range.replace(0, 1e-10)
        mf_mult = ((close - low) - (high - close)) / hl_range
        adl = (mf_mult * volume).cumsum()

        adl_ema = adl.ewm(span=p["ema_period"], adjust=False).mean()

        # Close trending up: current close > close 5 bars ago
        lookback = min(5, len(df) - 1)
        close_trending_up = float(close.iloc[-1]) > float(close.iloc[-1 - lookback])

        curr_adl = float(adl.iloc[-1])
        prev_adl = float(adl.iloc[-2])
        curr_adl_ema = float(adl_ema.iloc[-1])
        prev_adl_ema = float(adl_ema.iloc[-2])
        curr_close = float(close.iloc[-1])

        cross_up = prev_adl <= prev_adl_ema and curr_adl > curr_adl_ema
        cross_down = prev_adl >= prev_adl_ema and curr_adl < curr_adl_ema

        entry = cross_up and close_trending_up
        exit_ = cross_down

        confidence = 0.65 if entry else 0.0

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(confidence, 4),
            indicators={
                "adl": round(curr_adl, 2),
                "adl_ema": round(curr_adl_ema, 2),
                "close_trending_up": bool(close_trending_up),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "ema_period": (10, 30, 5),
        }


STRATEGY_CLASS = ADLStrategy

STRATEGIES = [
    ("PREBUILT-ADL", ADLStrategy, {"_asset": "BTC"}),
]
