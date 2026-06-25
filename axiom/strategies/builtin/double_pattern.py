"""Double top/bottom pattern detection strategy.

Detects two similar highs (double top) or two similar lows (double bottom)
within a tolerance window. Entry on double bottom confirmation when price
rises above the neckline.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "double_pattern"


class DoublePatternStrategy(BaseStrategy):
    """Double top/bottom detection strategy."""

    @property
    def name(self) -> str:
        return f"Double Pattern ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "lookback": 50,
            "tolerance_pct": 2.0,
            "neckline_break_pct": 0.5,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        lb = int(self.params.get("lookback", 50))
        tol = float(self.params.get("tolerance_pct", 1.5))
        return (
            f"Double top/bottom detection over {lb} bars with "
            f"{tol}% tolerance for matching peaks/troughs."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        lookback = int(self.params.get("lookback", 50))
        tolerance_pct = float(self.params.get("tolerance_pct", 1.5)) / 100.0
        neckline_break_pct = float(self.params.get("neckline_break_pct", 0.5)) / 100.0

        curr_close = float(df["close"].iloc[-1])

        if len(df) < lookback + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        window = df.iloc[-lookback:]
        lows = window["low"].values
        highs = window["high"].values

        # --- Double bottom detection (bullish) ---
        # Find two lowest troughs in the window
        half = lookback // 2
        first_half_lows = lows[:half]
        second_half_lows = lows[half:]

        first_low = float(np.min(first_half_lows))
        second_low = float(np.min(second_half_lows))

        double_bottom = False
        neckline = 0.0
        if first_low > 0:
            pct_diff = abs(first_low - second_low) / first_low
            if pct_diff <= tolerance_pct:
                # Neckline = highest high between the two lows
                neckline = float(np.max(highs[np.argmin(first_half_lows):half + np.argmin(second_half_lows)]))
                if neckline > 0:
                    double_bottom = curr_close > neckline * (1.0 + neckline_break_pct)

        # --- Double top detection (bearish exit) ---
        first_high = float(np.max(highs[:half]))
        second_high = float(np.max(highs[half:]))

        double_top = False
        if first_high > 0:
            pct_diff_top = abs(first_high - second_high) / first_high
            if pct_diff_top <= tolerance_pct:
                top_neckline = float(np.min(lows[np.argmax(highs[:half]):half + np.argmax(highs[half:])]))
                if top_neckline > 0:
                    double_top = curr_close < top_neckline * (1.0 - neckline_break_pct)

        entry = double_bottom
        exit_ = double_top

        conf = 0.0
        if entry and neckline > 0:
            conf = min((curr_close - neckline) / neckline * 100, 1.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(max(conf, 0.0), 4),
            indicators={
                "double_bottom": bool(double_bottom),
                "double_top": bool(double_top),
                "first_low": round(first_low, 4),
                "second_low": round(second_low, 4),
                "neckline": round(neckline, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "lookback": (20, 100, 10),
            "tolerance_pct": (0.5, 3.0, 0.5),
            "neckline_break_pct": (0.1, 1.5, 0.2),
        }


STRATEGY_CLASS = DoublePatternStrategy

STRATEGIES = [
    ("PREBUILT-DOUBLE-PATTERN", DoublePatternStrategy, {"_asset": "BTC"}),
]
