"""ROC (Rate of Change) strategy.

Entry: ROC crosses above 0 (upward momentum)
Exit: ROC crosses below 0
Compatible Regimes: TREND_UP, TREND_DOWN
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "roc"


class ROCStrategy(BaseStrategy):
    """Rate of Change momentum strategy.

    ROC = (close - close[n]) / close[n] * 100
    Entry when ROC crosses above threshold; exit when ROC crosses below
    threshold.
    """

    @property
    def name(self) -> str:
        return f"ROC({self.params.get('roc_period', 12)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "roc_period": 12,
            "threshold": 0.0,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when ROC({p['roc_period']}) crosses above {p['threshold']}. "
            f"Exits when ROC crosses below {p['threshold']}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        period = p["roc_period"]
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

        roc = (close - close.shift(period)) / close.shift(period) * 100.0

        curr_roc = float(roc.iloc[-1])
        prev_roc = float(roc.iloc[-2])
        curr_close = float(close.iloc[-1])

        threshold = p["threshold"]

        entry = prev_roc <= threshold and curr_roc > threshold
        exit_ = prev_roc >= threshold and curr_roc < threshold

        confidence = 0.0
        if entry:
            confidence = min(1.0, abs(curr_roc) / 5.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "roc": round(curr_roc, 4),
                "prev_roc": round(prev_roc, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "roc_period": (6, 20, 2),
            "threshold": (-2.0, 2.0, 0.5),
        }


STRATEGY_CLASS = ROCStrategy

STRATEGIES = [
    ("PREBUILT-ROC", ROCStrategy, {"_asset": "BTC"}),
]
