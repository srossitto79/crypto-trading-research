"""Chaikin Money Flow strategy."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "chaikin_mf"


class ChaikinMFStrategy(BaseStrategy):
    """Chaikin Money Flow (CMF) strategy.

    Entry: CMF crosses above threshold.
    Exit: CMF crosses below -threshold.
    """

    @property
    def name(self) -> str:
        return f"Chaikin MF {self.params.get('cmf_period', 20)} ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "cmf_period": 20,
            "threshold": 0.05,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when {p['cmf_period']}-bar CMF crosses above {p['threshold']}. "
            f"Exits when CMF crosses below -{p['threshold']}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        high = df["high"]
        low = df["low"]
        close = df["close"]
        volume = df["volume"]
        period = p["cmf_period"]

        if len(df) < period + 2:
            return Signal(entry_signal=False, exit_signal=False,
                          price=float(close.iloc[-1]), direction="long",
                          confidence=0.0, indicators={})

        # MF Multiplier: ((close - low) - (high - close)) / (high - low)
        hl_range = high - low
        hl_range = hl_range.replace(0, 1e-10)  # avoid division by zero
        mf_mult = ((close - low) - (high - close)) / hl_range
        mf_volume = mf_mult * volume

        cmf = mf_volume.rolling(window=period).sum() / volume.rolling(window=period).sum().replace(0, 1e-10)

        curr_cmf = float(cmf.iloc[-1])
        prev_cmf = float(cmf.iloc[-2])
        curr_close = float(close.iloc[-1])
        threshold = p["threshold"]

        cross_above = prev_cmf <= threshold and curr_cmf > threshold
        cross_below = prev_cmf >= -threshold and curr_cmf < -threshold

        entry = cross_above
        exit_ = cross_below

        confidence = min(1.0, abs(curr_cmf) / 0.2) if entry else 0.0

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(confidence, 4),
            indicators={
                "cmf": round(curr_cmf, 4),
                "threshold": threshold,
            },
        )

    def parameter_space(self) -> dict:
        return {
            "cmf_period": (10, 30, 5),
            "threshold": (0.02, 0.10, 0.02),
        }


STRATEGY_CLASS = ChaikinMFStrategy

STRATEGIES = [
    ("PREBUILT-CMF", ChaikinMFStrategy, {"_asset": "BTC"}),
]
