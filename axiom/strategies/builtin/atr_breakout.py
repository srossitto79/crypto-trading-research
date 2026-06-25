"""ATR Breakout strategy.

Entry: price breaks above previous close + atr_mult * ATR (breakout confirmation)
Exit: price falls below previous close - atr_mult * ATR
Compatible Regimes: TREND_UP, TREND_DOWN, BREAKOUT
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "atr_breakout"


class ATRBreakoutStrategy(BaseStrategy):
    """ATR-based breakout strategy.

    Measures N-period Average True Range and triggers entry when price
    closes beyond prev_close + atr_mult * ATR.  Exit fires on the
    opposite side breakout or when price retreats inside the channel.
    """

    @property
    def name(self) -> str:
        return (
            f"ATRBreakout({self.params.get('atr_period', 14)}, "
            f"{self.params.get('atr_mult', 2.0)}) ({self.asset})"
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
            "atr_period": 14,
            "atr_mult": 2.0,
            "lookback": 20,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN", "BREAKOUT"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when close > prev_close + {p['atr_mult']} * ATR({p['atr_period']}). "
            f"Exits when close < prev_close - {p['atr_mult']} * ATR({p['atr_period']}). "
            f"Lookback window: {p['lookback']} bars."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        atr_period = p["atr_period"]
        atr_mult = p["atr_mult"]
        lookback = p["lookback"]
        close = df["close"]

        min_len = max(atr_period, lookback) + 2
        if len(df) < min_len:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        high = df["high"]
        low = df["low"]

        # True Range
        prev_close_series = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close_series).abs(),
                (low - prev_close_series).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = tr.rolling(window=atr_period).mean()

        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        curr_atr = float(atr.iloc[-1])

        upper_band = prev_close + atr_mult * curr_atr
        lower_band = prev_close - atr_mult * curr_atr

        # Recent high/low over lookback for context
        recent_high = float(high.iloc[-lookback:].max())
        recent_low = float(low.iloc[-lookback:].min())

        entry = curr_close > upper_band
        exit_ = curr_close < lower_band

        # Direction: long on upper breakout, short on lower breakout
        direction = "long" if entry else "short" if exit_ else "long"

        confidence = 0.0
        if entry and curr_atr > 0:
            confidence = min(1.0, (curr_close - upper_band) / curr_atr)
        elif exit_ and curr_atr > 0:
            confidence = min(1.0, (lower_band - curr_close) / curr_atr)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(max(0.0, confidence), 4),
            indicators={
                "atr": round(curr_atr, 4),
                "upper_band": round(upper_band, 4),
                "lower_band": round(lower_band, 4),
                "recent_high": round(recent_high, 4),
                "recent_low": round(recent_low, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "atr_period": (10, 20, 2),
            "atr_mult": (1.5, 3.0, 0.5),
            "lookback": (10, 30, 5),
        }


STRATEGY_CLASS = ATRBreakoutStrategy

STRATEGIES = [
    ("PREBUILT-ATR-BREAKOUT", ATRBreakoutStrategy, {"_asset": "BTC"}),
]
