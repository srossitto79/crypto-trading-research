"""Dollar Strength Filter strategy — S082."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "dollar_strength_filter"


class DollarStrengthFilterStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Dollar Strength Filter ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "dxy_sma_period": 10,
            "rsi_period": 14,
            "rsi_oversold": 35,
            "rsi_overbought": 65,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Dollar-strength macro filter. Rising DXY SMA (slope >0 over "
            f"{p.get('dxy_sma_period', 10)}d) creates short bias for crypto. "
            f"Falling DXY creates long bias. RSI used for entry timing."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        rsi_period = int(p.get("rsi_period", 14))
        dxy_sma_period = int(p.get("dxy_sma_period", 10))

        # Compute RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - 100 / (1 + rs)
        curr_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

        if "dxy_close" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"dxy_slope": 0, "rsi": round(curr_rsi, 2)},
            )

        dxy = df["dxy_close"]
        dxy_sma = dxy.rolling(dxy_sma_period).mean()

        # Compute slope: difference between current and previous SMA
        dxy_slope = 0.0
        if len(dxy_sma.dropna()) >= 2:
            dxy_slope = float(dxy_sma.iloc[-1]) - float(dxy_sma.iloc[-2])

        rsi_oversold = float(p.get("rsi_oversold", 35))
        rsi_overbought = float(p.get("rsi_overbought", 65))

        entry = False
        direction = "long"
        confidence = 0.0

        if dxy_slope > 0:
            # Rising dollar -> short bias for crypto
            if curr_rsi > rsi_overbought:
                entry = True
                direction = "short"
                confidence = min((curr_rsi - rsi_overbought) / (100 - rsi_overbought), 1.0)
        elif dxy_slope < 0:
            # Falling dollar -> long bias for crypto
            if curr_rsi < rsi_oversold:
                entry = True
                direction = "long"
                confidence = min((rsi_oversold - curr_rsi) / rsi_oversold, 1.0)

        # Exit when RSI normalizes
        exit_signal = 40 < curr_rsi < 60

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "dxy_slope": round(dxy_slope, 6),
                "rsi": round(curr_rsi, 2),
                "bias": "short" if dxy_slope > 0 else ("long" if dxy_slope < 0 else "neutral"),
            },
        )


STRATEGY_CLASS = DollarStrengthFilterStrategy

STRATEGIES = [
    ("S082-DXY-BTC", DollarStrengthFilterStrategy, {"_asset": "BTC"}),
    ("S083-DXY-ETH", DollarStrengthFilterStrategy, {"_asset": "ETH"}),
]
