"""Williams %R recovery strategy with EMA support filter."""

from __future__ import annotations

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "williams_ema_reclaim"


def williams_r_series(df: pd.DataFrame, period: int) -> pd.Series:
    highest_high = df["high"].rolling(period).max()
    lowest_low = df["low"].rolling(period).min()
    return -100.0 * (highest_high - df["close"]) / (highest_high - lowest_low)


class WilliamsEMAReclaimStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Williams EMA Reclaim ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "wr_period": 14,
            "ema_period": 21,
            "wr_entry": -70.0,
            "wr_exit": -25.0,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Buy when Williams %R({p['wr_period']}) recovers through {p['wr_entry']} while price "
            f"is above EMA({p['ema_period']}); exit near momentum exhaustion at {p['wr_exit']}."
        )

    def _indicator_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        ema = close.ewm(span=int(self.params["ema_period"]), adjust=False).mean()
        wr = williams_r_series(df, int(self.params["wr_period"]))
        return pd.DataFrame({"close": close, "ema": ema, "wr": wr}, index=df.index)

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        indicators = self._indicator_frame(df)
        current = indicators.iloc[-1]
        previous = indicators.iloc[-2] if len(indicators) > 1 else current

        support_ok = bool(current["close"] >= current["ema"])
        entry = bool(previous["wr"] < float(self.params["wr_entry"]) and current["wr"] >= float(self.params["wr_entry"]) and support_ok)
        exit_ = bool(
            (previous["wr"] > float(self.params["wr_exit"]) and current["wr"] <= float(self.params["wr_exit"]))
            or current["close"] < current["ema"]
        )

        return Signal(
            entry_signal=entry,
            exit_signal=exit_,
            price=round(float(current["close"]), 4),
            direction="long",
            confidence=round(min(1.0, abs(float(current["wr"])) / 100.0), 4),
            indicators={
                "wr": round(float(current["wr"]), 2),
                "ema": round(float(current["ema"]), 4),
                "support_ok": support_ok,
            },
        )

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        indicators = self._indicator_frame(df)
        support_ok = indicators["close"] >= indicators["ema"]
        entry_signals = (indicators["wr"].shift(1) < float(self.params["wr_entry"])) & (
            indicators["wr"] >= float(self.params["wr_entry"])
        ) & support_ok
        exit_signals = (
            (indicators["wr"].shift(1) > float(self.params["wr_exit"]))
            & (indicators["wr"] <= float(self.params["wr_exit"]))
        ) | (indicators["close"] < indicators["ema"])
        return entry_signals.fillna(False), exit_signals.fillna(False)

    def parameter_space(self) -> dict:
        return {
            "wr_entry": (-85, -55, 5),
            "wr_exit": (-35, -10, 5),
            "ema_period": (10, 34, 3),
        }


STRATEGY_CLASS = WilliamsEMAReclaimStrategy

STRATEGIES = [
    ("PREBUILT-WILLIAMS-EMA-RECLAIM-BTC", WilliamsEMAReclaimStrategy, {"_asset": "BTC"}),
]
