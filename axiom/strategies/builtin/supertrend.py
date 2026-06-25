"""SuperTrend strategy."""
import pandas as pd
from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "supertrend"

class SuperTrendStrategy(BaseStrategy):
    @property
    def name(self) -> str: return f"SuperTrend ({self.asset})"
    @property
    def asset(self) -> str: return self.params.get("_asset", "BTC")
    @property
    def strategy_type(self) -> str: return TYPE_NAME
    @property
    def default_params(self) -> dict:
        return {"atr_period": 10, "multiplier": 3.0, "leverage": 3.0}
    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return f"Buys when price crosses above SuperTrend ({p['atr_period']}, {p['multiplier']}), sells when crosses below."

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        from axiom.scanner import atr
        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        
        atr_val = atr(df, p["atr_period"])
        hl2 = (high + low) / 2
        
        basic_upper = hl2 + p["multiplier"] * atr_val
        basic_lower = hl2 - p["multiplier"] * atr_val

        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        curr_upper = float(basic_upper.iloc[-1])
        curr_lower = float(basic_lower.iloc[-1])
        
        entry = prev_close <= curr_upper and curr_close > curr_upper
        exit_ = curr_close < curr_lower

        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction="long", confidence=1.0,
            indicators={"atr": round(float(atr_val.iloc[-1]), 4)}
        )

    def parameter_space(self) -> dict:
        return {"atr_period": (10, 20, 5), "multiplier": (2.0, 4.0, 0.5)}

STRATEGY_CLASS = SuperTrendStrategy
STRATEGIES = [("TOMB-SUPERTREND", SuperTrendStrategy, {"_asset": "BTC"})]
