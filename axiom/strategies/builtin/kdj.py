"""KDJ strategy.

K = SMA(RSV, k_smooth) where RSV = (close - lowest_low) / (highest_high - lowest_low) * 100
D = SMA(K, d_smooth)
J = 3*K - 2*D
Entry: J crosses above 0 from below AND K crosses above D
Exit: J crosses below 100 from above OR K crosses below D
Compatible Regimes: RANGE_BOUND, TREND_UP
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "kdj"


class KDJStrategy(BaseStrategy):
    """KDJ oscillator strategy.

    Extension of stochastic oscillator with a J line (3K - 2D) that provides
    early reversal signals.
    """

    @property
    def name(self) -> str:
        return f"KDJ({self.params.get('k_period', 9)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "k_period": 9,
            "k_smooth": 3,
            "d_smooth": 3,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"KDJ({p['k_period']}/{p['k_smooth']}/{p['d_smooth']}). "
            f"Enters when J crosses above 0 and K crosses above D. "
            f"Exits when J drops below 100 or K crosses below D."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        k_period = p["k_period"]
        k_smooth = p["k_smooth"]
        d_smooth = p["d_smooth"]

        min_bars = k_period + k_smooth + d_smooth + 2

        if len(df) < min_bars:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()
        hh_ll = (highest_high - lowest_low).replace(0, 1e-10)
        rsv = (close - lowest_low) / hh_ll * 100.0

        k_line = rsv.rolling(window=k_smooth).mean()
        d_line = k_line.rolling(window=d_smooth).mean()
        j_line = 3.0 * k_line - 2.0 * d_line

        curr_k = float(k_line.iloc[-1])
        prev_k = float(k_line.iloc[-2])
        curr_d = float(d_line.iloc[-1])
        prev_d = float(d_line.iloc[-2])
        curr_j = float(j_line.iloc[-1])
        prev_j = float(j_line.iloc[-2])
        curr_close = float(close.iloc[-1])

        j_cross_up = prev_j <= 0 and curr_j > 0
        k_cross_up = prev_k <= prev_d and curr_k > curr_d
        j_cross_down = prev_j >= 100 and curr_j < 100
        k_cross_down = prev_k >= prev_d and curr_k < curr_d

        entry = j_cross_up and k_cross_up
        exit_ = j_cross_down or k_cross_down

        confidence = 0.0
        if entry:
            confidence = min(1.0, (curr_k - curr_d) / 20.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "k": round(curr_k, 2),
                "d": round(curr_d, 2),
                "j": round(curr_j, 2),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "k_period": (5, 14, 1),
            "k_smooth": (2, 5, 1),
            "d_smooth": (2, 5, 1),
        }


STRATEGY_CLASS = KDJStrategy

STRATEGIES = [
    ("PREBUILT-KDJ", KDJStrategy, {"_asset": "BTC"}),
]
