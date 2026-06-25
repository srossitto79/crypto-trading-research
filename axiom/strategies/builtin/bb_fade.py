"""Bollinger Band Edge Fade strategy."""
import pandas as pd
from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "bb_fade"

class BBFadeStrategy(BaseStrategy):
    @property
    def name(self) -> str: return f"BB Edge Fade ({self.asset})"
    @property
    def asset(self) -> str: return self.params.get("_asset", "BTC")
    @property
    def strategy_type(self) -> str: return TYPE_NAME
    @property
    def default_params(self) -> dict:
        return {"bb_period": 20, "bb_std": 2.5, "leverage": 3.0}
    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}
    def describe(self) -> str:
        return "Mean reversion: fades moves that pierce the outer Bollinger Bands during a ranging market."
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        
        bb_mid = close.rolling(p["bb_period"]).mean()
        bb_std = close.rolling(p["bb_period"]).std()
        bb_lower = bb_mid - p["bb_std"] * bb_std
        
        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        
        curr_lower = float(bb_lower.iloc[-1])
        prev_lower = float(bb_lower.iloc[-2])
        
        entry = prev_close < prev_lower and curr_close > curr_lower
        exit_ = curr_close >= float(bb_mid.iloc[-1])
        
        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction="long", confidence=1.0,
            indicators={"bb_lower": round(curr_lower, 4)}
        )
    def parameter_space(self) -> dict:
        return {"bb_period": (15, 25, 5), "bb_std": (2.0, 3.0, 0.5)}

STRATEGY_CLASS = BBFadeStrategy
STRATEGIES = [("TOMB-BBFADE", BBFadeStrategy, {"_asset": "BTC"})]
