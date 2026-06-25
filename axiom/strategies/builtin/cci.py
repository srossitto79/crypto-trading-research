"""CCI (Commodity Channel Index) strategy.

Entry: CCI crosses above -100 from below (oversold bounce)
Exit: CCI crosses below +100 from above
Compatible Regimes: RANGE_BOUND, TREND_UP
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "cci"


class CCIStrategy(BaseStrategy):
    """Commodity Channel Index mean-reversion strategy.

    CCI = (Typical Price - SMA(TP)) / (0.015 * Mean Deviation)
    Entry when CCI crosses above oversold from below; exit when CCI crosses
    below overbought from above.
    """

    @property
    def name(self) -> str:
        return f"CCI({self.params.get('cci_period', 20)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "cci_period": 20,
            "oversold": -100,
            "overbought": 100,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when CCI({p['cci_period']}) crosses above {p['oversold']} "
            f"(oversold bounce). Exits when CCI crosses below {p['overbought']}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        period = p["cci_period"]
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

        tp = (df["high"] + df["low"] + close) / 3.0
        tp_sma = tp.rolling(window=period).mean()
        mean_dev = tp.rolling(window=period).apply(
            lambda x: np.mean(np.abs(x - x.mean())), raw=True
        )
        cci = (tp - tp_sma) / (0.015 * mean_dev)

        curr_cci = float(cci.iloc[-1])
        prev_cci = float(cci.iloc[-2])
        curr_close = float(close.iloc[-1])

        oversold = p["oversold"]
        overbought = p["overbought"]

        entry = prev_cci <= oversold and curr_cci > oversold
        exit_ = prev_cci >= overbought and curr_cci < overbought

        confidence = 0.0
        if entry:
            confidence = min(1.0, abs(curr_cci - oversold) / 50.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "cci": round(curr_cci, 2),
                "prev_cci": round(prev_cci, 2),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "cci_period": (10, 30, 5),
            "oversold": (-150, -50, 25),
            "overbought": (50, 150, 25),
        }


STRATEGY_CLASS = CCIStrategy

STRATEGIES = [
    ("PREBUILT-CCI", CCIStrategy, {"_asset": "BTC"}),
]
