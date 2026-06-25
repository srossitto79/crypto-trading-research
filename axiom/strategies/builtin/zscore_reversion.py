"""Z-score mean reversion strategy.

Computes z = (close - rolling_mean) / rolling_std. Enters long when z drops
below -entry_z (oversold). Exits when z crosses back above exit_z (mean).
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "zscore_reversion"


class ZScoreReversionStrategy(BaseStrategy):
    """Z-score mean reversion strategy."""

    @property
    def name(self) -> str:
        return f"Z-Score Reversion ({self.asset})"

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
            "entry_threshold": 2.0,
            "exit_threshold": 0.0,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}

    def describe(self) -> str:
        period = int(self.params.get("period", 20))
        entry_t = float(self.params.get("entry_threshold", 2.0))
        exit_t = float(self.params.get("exit_threshold", 0.0))
        return (
            f"Z-score mean reversion: {period}-period z-score, "
            f"enter when z < -{entry_t}, exit when z > {exit_t}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        period = int(self.params.get("period", 20))
        entry_z = float(self.params.get("entry_threshold", 2.0))
        exit_z = float(self.params.get("exit_threshold", 0.0))

        curr_close = float(df["close"].iloc[-1])

        if len(df) < period + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        rolling_mean = df["close"].rolling(period).mean()
        rolling_std = df["close"].rolling(period).std()

        mean_val = rolling_mean.iloc[-1]
        std_val = rolling_std.iloc[-1]

        if pd.isna(mean_val) or pd.isna(std_val) or std_val == 0:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        z = (curr_close - float(mean_val)) / float(std_val)

        entry = z < -entry_z
        exit_ = z > exit_z

        conf = 0.0
        if entry:
            conf = min(abs(z) / (entry_z * 2), 1.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(conf, 4),
            indicators={
                "z_score": round(z, 4),
                "rolling_mean": round(float(mean_val), 4),
                "rolling_std": round(float(std_val), 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "period": (10, 50, 5),
            "entry_threshold": (1.5, 3.0, 0.5),
            "exit_threshold": (-0.5, 0.5, 0.5),
        }


STRATEGY_CLASS = ZScoreReversionStrategy

STRATEGIES = [
    ("PREBUILT-ZSCORE", ZScoreReversionStrategy, {"_asset": "BTC"}),
]
