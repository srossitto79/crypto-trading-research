"""Crowding Detector Contrarian strategy — S072."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "crowding_detector"


class CrowdingDetectorStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Crowding Detector Contrarian ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "ls_extreme_long": 2.0,
            "ls_extreme_short": 0.5,
            "oi_growth_pct": 10,
            "lookback_hours": 24,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Contrarian strategy that fades extreme crowd positioning. "
            f"Shorts when ls_ratio > {p.get('ls_extreme_long', 2.0)} with rising OI, "
            f"longs when ls_ratio < {p.get('ls_extreme_short', 0.5)} with rising OI."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        lookback = int(p.get("lookback_hours", 24))
        ls_long = float(p.get("ls_extreme_long", 2.0))
        ls_short = float(p.get("ls_extreme_short", 0.5))
        oi_growth = float(p.get("oi_growth_pct", 10))

        if "ls_ratio" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"ls_ratio": 0, "oi_change_pct": 0},
            )

        curr_ls = float(df["ls_ratio"].iloc[-1]) if pd.notna(df["ls_ratio"].iloc[-1]) else 1.0

        # Open interest growth
        oi_change_pct = 0.0
        if "open_interest" in df.columns and len(df) > lookback:
            oi_now = float(df["open_interest"].iloc[-1])
            oi_prev = float(df["open_interest"].iloc[-lookback])
            if oi_prev > 0:
                oi_change_pct = ((oi_now - oi_prev) / oi_prev) * 100

        oi_rising = oi_change_pct > oi_growth

        # Extreme long crowding + OI rising -> short (contrarian)
        entry_short = curr_ls > ls_long and oi_rising
        # Extreme short crowding + OI rising -> long (contrarian)
        entry_long = curr_ls < ls_short and oi_rising

        entry = entry_short or entry_long
        direction = "short" if entry_short else "long"
        confidence = 0.0
        if entry_short:
            confidence = min((curr_ls - ls_long) / ls_long, 1.0)
        elif entry_long:
            confidence = min((ls_short - curr_ls) / ls_short, 1.0)

        # Exit when ls_ratio normalizes
        exit_signal = 0.8 < curr_ls < 1.2

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "ls_ratio": round(curr_ls, 4),
                "oi_change_pct": round(oi_change_pct, 4),
                "oi_rising": bool(oi_rising),
            },
        )


STRATEGY_CLASS = CrowdingDetectorStrategy

STRATEGIES = [
    ("S072-CROWD-BTC", CrowdingDetectorStrategy, {"_asset": "BTC"}),
    ("S073-CROWD-ETH", CrowdingDetectorStrategy, {"_asset": "ETH"}),
]
