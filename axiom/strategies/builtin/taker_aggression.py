"""Taker Aggression Momentum strategy — S074."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "taker_aggression"


class TakerAggressionStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Taker Aggression Momentum ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "taker_threshold_long": 1.5,
            "taker_threshold_short": 0.67,
            "consecutive_bars": 3,
            "ema_period": 20,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Momentum strategy using taker buy/sell ratio. "
            f"Enters long when ratio sustains above {p.get('taker_threshold_long', 1.5)} "
            f"for {p.get('consecutive_bars', 3)} bars with EMA confirmation. "
            f"Enters short when ratio below {p.get('taker_threshold_short', 0.67)}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        thresh_long = float(p.get("taker_threshold_long", 1.5))
        thresh_short = float(p.get("taker_threshold_short", 0.67))
        consec = int(p.get("consecutive_bars", 3))
        ema_period = int(p.get("ema_period", 20))

        if "taker_buy_sell_ratio" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"taker_ratio": 0, "ema": 0, "consecutive_above": 0},
            )

        taker = df["taker_buy_sell_ratio"]
        ema = close.ewm(span=ema_period, adjust=False).mean()
        curr_ema = float(ema.iloc[-1])

        # Count consecutive bars above/below threshold
        recent = taker.iloc[-consec:] if len(taker) >= consec else taker
        consec_above = int((recent > thresh_long).all()) if len(recent) == consec else 0
        consec_below = int((recent < thresh_short).all()) if len(recent) == consec else 0

        # EMA confirmation
        price_above_ema = curr_close > curr_ema
        price_below_ema = curr_close < curr_ema

        entry_long = bool(consec_above) and price_above_ema
        entry_short = bool(consec_below) and price_below_ema

        entry = entry_long or entry_short
        direction = "long" if entry_long else "short"

        curr_taker = float(taker.iloc[-1]) if pd.notna(taker.iloc[-1]) else 1.0
        confidence = 0.0
        if entry_long:
            confidence = min((curr_taker - thresh_long) / thresh_long, 1.0)
        elif entry_short:
            confidence = min((thresh_short - curr_taker) / thresh_short, 1.0)

        # Exit when taker ratio normalizes
        exit_signal = 0.8 < curr_taker < 1.2

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(max(confidence, 0.0), 4),
            indicators={
                "taker_ratio": round(curr_taker, 4),
                "ema": round(curr_ema, 4),
                "consecutive_above": consec_above,
                "consecutive_below": consec_below,
            },
        )


STRATEGY_CLASS = TakerAggressionStrategy

STRATEGIES = [
    ("S074-TAKER-BTC", TakerAggressionStrategy, {"_asset": "BTC"}),
    ("S075-TAKER-ETH", TakerAggressionStrategy, {"_asset": "ETH"}),
]
