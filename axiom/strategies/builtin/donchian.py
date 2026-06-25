"""Donchian Channel breakout strategy."""

from __future__ import annotations

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "donchian"


def resolve_donchian_period(params: dict | None) -> int:
    """Support both the current and legacy Donchian period keys."""
    payload = params or {}
    # BaseStrategy merges default params first, so prefer the legacy key when present
    # instead of letting the default ``period`` silently override it.
    raw_value = payload.get("donchian_period", payload.get("period", 20))
    try:
        period = int(raw_value)
    except (TypeError, ValueError):
        period = 20
    return max(period, 2)


def donchian_bands(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return prior-bar Donchian bands to avoid lookahead in both live and backtest paths."""
    upper = df["high"].rolling(period).max().shift(1)
    lower = df["low"].rolling(period).min().shift(1)
    middle = (upper + lower) / 2.0
    return upper, middle, lower


class DonchianStrategy(BaseStrategy):
    """Donchian Channel Breakout strategy."""

    @property
    def name(self) -> str:
        return f"Donchian Channel ({self.asset})"

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
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        period = resolve_donchian_period(self.params)
        return f"Donchian Channel breakout ({period}-period lookback)."

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        period = resolve_donchian_period(self.params)
        curr_close = float(df["close"].iloc[-1])

        if len(df) < period + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        upper_prev, middle_prev, lower_prev = donchian_bands(df, period)
        prev_close = float(df["close"].iloc[-2])

        curr_upper = upper_prev.iloc[-1]
        curr_middle = middle_prev.iloc[-1]
        curr_lower = lower_prev.iloc[-1]
        if pd.isna(curr_upper) or pd.isna(curr_lower):
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        upper_value = float(curr_upper)
        middle_value = float(curr_middle) if not pd.isna(curr_middle) else (upper_value + float(curr_lower)) / 2.0
        lower_value = float(curr_lower)

        entry = prev_close <= upper_value and curr_close > upper_value
        exit_ = prev_close >= lower_value and curr_close < lower_value

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=1.0 if entry else 0.0,
            indicators={
                "upper": round(upper_value, 4),
                "lower": round(lower_value, 4),
                "middle": round(middle_value, 4),
            },
        )

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Vectorized signal generation for backtesting."""
        period = resolve_donchian_period(self.params)
        upper_prev, _, lower_prev = donchian_bands(df, period)
        close = df["close"]
        close_prev = close.shift(1)

        entry_signals = (close_prev <= upper_prev) & (close > upper_prev)
        exit_signals = (close_prev >= lower_prev) & (close < lower_prev)

        return entry_signals.fillna(False), exit_signals.fillna(False)

    def parameter_space(self) -> dict:
        return {"period": (10, 40, 5)}


STRATEGY_CLASS = DonchianStrategy

STRATEGIES = [
    ("SXXX-DONCHIAN-BTC", DonchianStrategy, {"_asset": "BTC"}),
]
