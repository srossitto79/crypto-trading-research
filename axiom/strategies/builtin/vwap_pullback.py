"""VWAP Pullback strategy."""
import pandas as pd
from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "vwap_pullback"

class VWAPPullbackStrategy(BaseStrategy):
    @property
    def name(self) -> str: return f"VWAP Pullback ({self.asset})"
    @property
    def asset(self) -> str: return self.params.get("_asset", "BTC")
    @property
    def strategy_type(self) -> str: return TYPE_NAME
    @property
    def default_params(self) -> dict:
        return {"distance_pct": 0.02, "leverage": 3.0}
    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "RANGE_BOUND"}
    def describe(self) -> str:
        return "Trades pullbacks to the VWAP baseline after an aggressive spike."
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        close = df["close"]
        vol = df.get("volume", df["close"] * 0 + 1)
        
        typ_price = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (typ_price * vol).rolling(20).sum() / vol.rolling(20).sum()
        
        curr_close = float(close.iloc[-1])
        curr_vwap = float(vwap.iloc[-1])
        
        dist = (curr_vwap - curr_close) / curr_vwap
        
        entry = dist > self.params["distance_pct"]
        exit_ = curr_close >= curr_vwap
        
        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction="long", confidence=1.0,
            indicators={"vwap": round(curr_vwap, 4)}
        )
    def parameter_space(self) -> dict:
        return {"distance_pct": (0.01, 0.05, 0.01)}

STRATEGY_CLASS = VWAPPullbackStrategy
STRATEGIES = [("TOMB-VWAP", VWAPPullbackStrategy, {"_asset": "BTC"})]
