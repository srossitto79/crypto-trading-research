"""Fear & Greed Contrarian strategy — S078."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "fear_greed_contrarian"


class FearGreedContrarianStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Fear & Greed Contrarian ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "fear_threshold": 20,
            "greed_threshold": 80,
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Macro-filtered contrarian strategy. "
            f"Buys when fear_greed < {p.get('fear_threshold', 20)} and RSI oversold. "
            f"Shorts when fear_greed > {p.get('greed_threshold', 80)} and RSI overbought."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        rsi_period = int(p.get("rsi_period", 14))

        # Compute RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - 100 / (1 + rs)
        curr_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

        if "fear_greed" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"fear_greed": 0, "rsi": round(curr_rsi, 2)},
            )

        curr_fg = float(df["fear_greed"].iloc[-1]) if pd.notna(df["fear_greed"].iloc[-1]) else 50.0
        fear_thresh = float(p.get("fear_threshold", 20))
        greed_thresh = float(p.get("greed_threshold", 80))
        rsi_oversold = float(p.get("rsi_oversold", 30))
        rsi_overbought = float(p.get("rsi_overbought", 70))

        # Extreme fear + RSI oversold -> buy
        entry_long = curr_fg < fear_thresh and curr_rsi < rsi_oversold
        # Extreme greed + RSI overbought -> short
        entry_short = curr_fg > greed_thresh and curr_rsi > rsi_overbought

        entry = entry_long or entry_short
        direction = "long" if entry_long else "short"

        confidence = 0.0
        if entry_long:
            confidence = min((fear_thresh - curr_fg) / fear_thresh, 1.0)
        elif entry_short:
            confidence = min((curr_fg - greed_thresh) / (100 - greed_thresh), 1.0)

        # Exit when sentiment normalizes
        exit_signal = 40 < curr_fg < 60

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "fear_greed": round(curr_fg, 2),
                "rsi": round(curr_rsi, 2),
            },
        )


STRATEGY_CLASS = FearGreedContrarianStrategy

STRATEGIES = [
    ("S078-FG-BTC", FearGreedContrarianStrategy, {"_asset": "BTC"}),
    ("S079-FG-ETH", FearGreedContrarianStrategy, {"_asset": "ETH"}),
]
