"""RSI divergence detection strategy.

Detects bullish divergence where price makes a lower low but RSI makes a
higher low, signalling potential reversal upward.
"""

import numpy as np
import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "rsi_divergence"


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Compute RSI using exponential moving average of gains/losses."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


class RSIDivergenceStrategy(BaseStrategy):
    """RSI divergence detection strategy."""

    @property
    def name(self) -> str:
        return f"RSI Divergence ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "rsi_period": 14,
            "lookback": 10,
            "oversold": 30,
            "overbought": 70,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        rsi_p = int(self.params.get("rsi_period", 14))
        lb = int(self.params.get("lookback", 20))
        return (
            f"RSI divergence: detects bullish divergence using {rsi_p}-period "
            f"RSI over a {lb}-bar lookback window."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        rsi_period = int(self.params.get("rsi_period", 14))
        lookback = int(self.params.get("lookback", 10))
        oversold = float(self.params.get("oversold", 30))
        overbought = float(self.params.get("overbought", 70))

        curr_close = float(df["close"].iloc[-1])
        min_bars = rsi_period + lookback + 2

        if len(df) < min_bars:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        rsi = _compute_rsi(df["close"], rsi_period)
        curr_rsi = rsi.iloc[-1]

        if pd.isna(curr_rsi):
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        # Look for bullish divergence in the lookback window
        window_close = df["close"].iloc[-lookback:]
        window_rsi = rsi.iloc[-lookback:]

        # Find the two lowest price troughs (split window in half)
        half = lookback // 2
        first_half_idx = window_close.iloc[:half].idxmin()
        second_half_idx = window_close.iloc[half:].idxmin()

        price_low_1 = float(window_close.loc[first_half_idx])
        price_low_2 = float(window_close.loc[second_half_idx])

        rsi_low_1 = float(window_rsi.loc[first_half_idx])
        rsi_low_2 = float(window_rsi.loc[second_half_idx])

        # Bullish divergence: price lower low, RSI higher low
        price_lower_low = price_low_2 < price_low_1
        rsi_higher_low = rsi_low_2 > rsi_low_1

        bullish_divergence = price_lower_low and rsi_higher_low

        # Entry: bullish divergence AND RSI < oversold
        entry = bullish_divergence and float(curr_rsi) < oversold
        # Exit: RSI > overbought
        exit_ = float(curr_rsi) > overbought

        conf = 0.0
        if entry and price_low_1 > 0:
            rsi_diff = rsi_low_2 - rsi_low_1
            conf = min(rsi_diff / 20.0, 1.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(max(conf, 0.0), 4),
            indicators={
                "rsi": round(float(curr_rsi), 4),
                "bullish_divergence": bool(bullish_divergence),
                "price_low_recent": round(price_low_2, 4),
                "rsi_low_recent": round(rsi_low_2, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "rsi_period": (7, 21, 7),
            "lookback": (5, 20, 5),
            "oversold": (20, 40, 5),
            "overbought": (60, 80, 5),
        }


STRATEGY_CLASS = RSIDivergenceStrategy

STRATEGIES = [
    ("PREBUILT-RSI-DIV", RSIDivergenceStrategy, {"_asset": "BTC"}),
]
