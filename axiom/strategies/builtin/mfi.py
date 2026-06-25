"""MFI (Money Flow Index) strategy.

Entry: MFI crosses above oversold level
Exit: MFI crosses below overbought level
Compatible Regimes: RANGE_BOUND, TREND_UP
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "mfi"


class MFIStrategy(BaseStrategy):
    """Money Flow Index volume-weighted RSI strategy.

    MFI = 100 - (100 / (1 + positive_flow / negative_flow))
    Entry when MFI crosses above oversold; exit when MFI crosses below
    overbought.
    """

    @property
    def name(self) -> str:
        return f"MFI({self.params.get('mfi_period', 14)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "mfi_period": 14,
            "oversold": 20,
            "overbought": 80,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when MFI({p['mfi_period']}) crosses above {p['oversold']}. "
            f"Exits when MFI crosses below {p['overbought']}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        period = p["mfi_period"]
        close = df["close"]

        if len(df) < period + 3:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        tp = (df["high"] + df["low"] + close) / 3.0
        raw_mf = tp * df["volume"]

        tp_diff = tp.diff()
        pos_flow = raw_mf.where(tp_diff > 0, 0.0).rolling(window=period).sum()
        neg_flow = raw_mf.where(tp_diff < 0, 0.0).rolling(window=period).sum()

        mfi = 100.0 - (100.0 / (1.0 + pos_flow / neg_flow.replace(0, 1e-10)))

        curr_mfi = float(mfi.iloc[-1])
        prev_mfi = float(mfi.iloc[-2])
        curr_close = float(close.iloc[-1])

        oversold = p["oversold"]
        overbought = p["overbought"]

        entry = prev_mfi <= oversold and curr_mfi > oversold
        exit_ = prev_mfi >= overbought and curr_mfi < overbought

        confidence = 0.0
        if entry:
            confidence = min(1.0, (curr_mfi - oversold) / 20.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "mfi": round(curr_mfi, 2),
                "prev_mfi": round(prev_mfi, 2),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "mfi_period": (10, 20, 2),
            "oversold": (10, 30, 5),
            "overbought": (70, 90, 5),
        }


STRATEGY_CLASS = MFIStrategy

STRATEGIES = [
    ("PREBUILT-MFI", MFIStrategy, {"_asset": "BTC"}),
]
