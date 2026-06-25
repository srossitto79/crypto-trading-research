"""Liquidation Cascade Mean Reversion strategy — S070."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "liquidation_cascade"


class LiquidationCascadeStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Liquidation Cascade Mean Reversion ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "liq_zscore_threshold": 2.5,
            "lookback_hours": 24,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL"}

    def describe(self) -> str:
        p = self.params
        thresh = p.get("liq_zscore_threshold", 2.5)
        return (
            f"Fades liquidation cascades when long_liq_usd or short_liq_usd "
            f"spikes above {thresh}\u03c3 on a rolling 24h window. "
            f"Mean-reversion entry expecting price to snap back."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        lookback = int(p.get("lookback_hours", 24))
        threshold = float(p.get("liq_zscore_threshold", 2.5))

        # Need at least one liquidation column
        has_long_liq = "long_liq_usd" in df.columns
        has_short_liq = "short_liq_usd" in df.columns

        if not has_long_liq and not has_short_liq:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"long_liq_zscore": 0, "short_liq_zscore": 0},
            )

        long_liq_z = 0.0
        short_liq_z = 0.0

        if has_long_liq:
            col = df["long_liq_usd"]
            rolling_mean = col.rolling(lookback).mean()
            rolling_std = col.rolling(lookback).std()
            z = (col - rolling_mean) / rolling_std.replace(0, float("nan"))
            long_liq_z = float(z.iloc[-1]) if pd.notna(z.iloc[-1]) else 0.0

        if has_short_liq:
            col = df["short_liq_usd"]
            rolling_mean = col.rolling(lookback).mean()
            rolling_std = col.rolling(lookback).std()
            z = (col - rolling_mean) / rolling_std.replace(0, float("nan"))
            short_liq_z = float(z.iloc[-1]) if pd.notna(z.iloc[-1]) else 0.0

        # Long liquidation spike -> price dumped -> fade with long
        entry_long = long_liq_z > threshold
        # Short liquidation spike -> price pumped -> fade with short
        entry_short = short_liq_z > threshold

        entry = entry_long or entry_short
        direction = "long" if entry_long else "short"
        confidence = max(abs(long_liq_z), abs(short_liq_z)) / (threshold * 2)
        confidence = min(confidence, 1.0)

        exit_signal = abs(long_liq_z) < 0.5 and abs(short_liq_z) < 0.5

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "long_liq_zscore": round(long_liq_z, 4),
                "short_liq_zscore": round(short_liq_z, 4),
            },
        )


STRATEGY_CLASS = LiquidationCascadeStrategy

STRATEGIES = [
    ("S070-LIQ-BTC", LiquidationCascadeStrategy, {"_asset": "BTC"}),
    ("S071-LIQ-ETH", LiquidationCascadeStrategy, {"_asset": "ETH"}),
]
