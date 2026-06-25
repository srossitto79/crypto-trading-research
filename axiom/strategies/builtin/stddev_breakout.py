"""Standard Deviation / Z-Score Mean Reversion strategy.

Entry: Z-score drops below -threshold (oversold, mean-reversion buy)
Exit: Z-score rises above +threshold (overbought, take profit)
Compatible Regimes: RANGE_BOUND, MEAN_REVERSION
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "stddev_breakout"


class StdDevBreakoutStrategy(BaseStrategy):
    """Z-score mean reversion strategy.

    Computes a rolling z-score: (close - SMA) / rolling_stddev.
    Enters long when z-score falls below the negative threshold (oversold)
    and exits when z-score rises above the positive threshold (overbought /
    mean achieved).
    """

    @property
    def name(self) -> str:
        return (
            f"ZScore({self.params.get('period', 20)}, "
            f"{self.params.get('z_threshold', 2.0)}) ({self.asset})"
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
            "period": 20,
            "z_threshold": 2.0,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "MEAN_REVERSION"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Z-score = (close - SMA({p['period']})) / StdDev({p['period']}). "
            f"Enters long when z < -{p['z_threshold']} (oversold). "
            f"Exits when z > +{p['z_threshold']} (overbought / profit target)."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        period = p["period"]
        z_threshold = p["z_threshold"]
        close = df["close"]

        if len(df) < period + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        sma = close.rolling(window=period).mean()
        stddev = close.rolling(window=period).std(ddof=1)

        # Avoid division by zero
        zscore = pd.Series(
            np.where(stddev > 0, (close - sma) / stddev, 0.0),
            index=close.index,
        )

        curr_z = float(zscore.iloc[-1])
        prev_z = float(zscore.iloc[-2])
        curr_close = float(close.iloc[-1])
        curr_sma = float(sma.iloc[-1])
        curr_std = float(stddev.iloc[-1])

        # Entry: z-score crosses below -threshold (oversold, expect reversion up)
        entry = prev_z >= -z_threshold and curr_z < -z_threshold

        # Exit: z-score crosses above +threshold (overbought / target hit)
        exit_ = prev_z <= z_threshold and curr_z > z_threshold

        direction = "long"

        confidence = 0.0
        if entry:
            confidence = min(1.0, (abs(curr_z) - z_threshold) / z_threshold + 0.5)
        elif exit_:
            confidence = min(1.0, (curr_z - z_threshold) / z_threshold + 0.5)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(max(0.0, confidence), 4),
            indicators={
                "zscore": round(curr_z, 4),
                "prev_zscore": round(prev_z, 4),
                "sma": round(curr_sma, 4),
                "stddev": round(curr_std, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "period": (10, 30, 5),
            "z_threshold": (1.5, 3.0, 0.5),
        }


STRATEGY_CLASS = StdDevBreakoutStrategy

STRATEGIES = [
    ("PREBUILT-STDDEV", StdDevBreakoutStrategy, {"_asset": "BTC"}),
]
