"""Connors RSI strategy.

CRSI = (RSI(close, n) + RSI(streak, n) + PercentRank(ROC, n)) / 3
Entry: CRSI crosses above oversold
Exit: CRSI crosses below overbought
Compatible Regimes: RANGE_BOUND
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "connors_rsi"


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """Compute RSI using exponential moving average of gains/losses."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


def _streak(close: pd.Series) -> pd.Series:
    """Compute consecutive up/down day streak."""
    diff = close.diff()
    streak = pd.Series(0.0, index=close.index)
    for i in range(1, len(close)):
        if diff.iloc[i] > 0:
            streak.iloc[i] = max(streak.iloc[i - 1], 0) + 1
        elif diff.iloc[i] < 0:
            streak.iloc[i] = min(streak.iloc[i - 1], 0) - 1
        else:
            streak.iloc[i] = 0
    return streak


def _percent_rank(series: pd.Series, period: int) -> pd.Series:
    """Compute percent rank of current value over lookback window."""
    def _rank(x):
        if len(x) < 2:
            return 50.0
        current = x[-1]
        past = x[:-1]
        return np.sum(past < current) / len(past) * 100.0

    return series.rolling(window=period).apply(_rank, raw=True)


class ConnorsRSIStrategy(BaseStrategy):
    """Connors RSI mean-reversion strategy.

    CRSI averages three components: standard RSI, streak RSI, and percent
    rank of rate of change to identify short-term reversals.
    """

    @property
    def name(self) -> str:
        return f"ConnorsRSI({self.params.get('rsi_period', 3)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "rsi_period": 3,
            "streak_period": 2,
            "rank_period": 100,
            "oversold": 10,
            "overbought": 90,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Connors RSI({p['rsi_period']}/{p['streak_period']}/{p['rank_period']}). "
            f"Enters when CRSI crosses above {p['oversold']}; "
            f"exits when CRSI crosses below {p['overbought']}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        min_bars = max(p["rsi_period"], p["streak_period"], p["rank_period"]) + 5

        if len(df) < min_bars:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        rsi_close = _rsi(close, p["rsi_period"])
        streak_vals = _streak(close)
        rsi_streak = _rsi(streak_vals, p["streak_period"])
        roc = (close - close.shift(1)) / close.shift(1) * 100.0
        pct_rank = _percent_rank(roc, p["rank_period"])

        crsi = (rsi_close + rsi_streak + pct_rank) / 3.0

        curr_crsi = float(crsi.iloc[-1])
        prev_crsi = float(crsi.iloc[-2])
        curr_close = float(close.iloc[-1])

        oversold = p["oversold"]
        overbought = p["overbought"]

        entry = prev_crsi <= oversold and curr_crsi > oversold
        exit_ = prev_crsi >= overbought and curr_crsi < overbought

        confidence = 0.0
        if entry:
            confidence = min(1.0, (oversold - prev_crsi + 10) / 20.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "crsi": round(curr_crsi, 2),
                "rsi_close": round(float(rsi_close.iloc[-1]), 2),
                "rsi_streak": round(float(rsi_streak.iloc[-1]), 2),
                "pct_rank": round(float(pct_rank.iloc[-1]), 2),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "rsi_period": (2, 5, 1),
            "streak_period": (2, 4, 1),
            "rank_period": (50, 150, 25),
            "oversold": (5, 20, 5),
            "overbought": (80, 95, 5),
        }


STRATEGY_CLASS = ConnorsRSIStrategy

STRATEGIES = [
    ("PREBUILT-CONNORS-RSI", ConnorsRSIStrategy, {"_asset": "BTC"}),
]
