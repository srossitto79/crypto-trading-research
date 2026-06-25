"""Ultimate Oscillator strategy.

UO = weighted average of buying pressure / true range across 3 periods.
Entry: UO crosses above oversold
Exit: UO crosses above overbought then drops
Compatible Regimes: RANGE_BOUND, TREND_UP
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "ultimate_oscillator"


class UltimateOscillatorStrategy(BaseStrategy):
    """Ultimate Oscillator multi-timeframe momentum strategy.

    UO = 100 * (4 * avg7 + 2 * avg14 + avg28) / 7
    where avg = sum(BP) / sum(TR) over each period.
    """

    @property
    def name(self) -> str:
        p = self.params
        return (
            f"UO({p.get('period1', 7)}/{p.get('period2', 14)}/{p.get('period3', 28)}) "
            f"({self.asset})"
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
            "period1": 7,
            "period2": 14,
            "period3": 28,
            "oversold": 30,
            "overbought": 70,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Ultimate Oscillator({p['period1']}/{p['period2']}/{p['period3']}). "
            f"Enters when UO crosses above {p['oversold']}; "
            f"exits when UO crosses above {p['overbought']} then drops."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        max_period = p["period3"]

        if len(df) < max_period + 3:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        prev_close = close.shift(1)
        bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
        tr = pd.concat([high, prev_close], axis=1).max(axis=1) - pd.concat(
            [low, prev_close], axis=1
        ).min(axis=1)
        tr = tr.replace(0, 1e-10)

        avg1 = bp.rolling(window=p["period1"]).sum() / tr.rolling(window=p["period1"]).sum()
        avg2 = bp.rolling(window=p["period2"]).sum() / tr.rolling(window=p["period2"]).sum()
        avg3 = bp.rolling(window=p["period3"]).sum() / tr.rolling(window=p["period3"]).sum()

        uo = 100.0 * (4.0 * avg1 + 2.0 * avg2 + avg3) / 7.0

        curr_uo = float(uo.iloc[-1])
        prev_uo = float(uo.iloc[-2])
        prev2_uo = float(uo.iloc[-3])
        curr_close = float(close.iloc[-1])

        oversold = p["oversold"]
        overbought = p["overbought"]

        entry = prev_uo <= oversold and curr_uo > oversold
        exit_ = prev2_uo < overbought and prev_uo >= overbought and curr_uo < prev_uo

        confidence = 0.0
        if entry:
            confidence = min(1.0, (curr_uo - oversold) / 15.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "uo": round(curr_uo, 2),
                "prev_uo": round(prev_uo, 2),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "period1": (5, 10, 1),
            "period2": (10, 20, 2),
            "period3": (20, 35, 5),
            "oversold": (20, 40, 5),
            "overbought": (60, 80, 5),
        }


STRATEGY_CLASS = UltimateOscillatorStrategy

STRATEGIES = [
    ("PREBUILT-ULT-OSC", UltimateOscillatorStrategy, {"_asset": "BTC"}),
]
