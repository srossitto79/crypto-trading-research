"""Opening Range Breakout strategy."""
import pandas as pd
from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "orb"

class ORBStrategy(BaseStrategy):
    @property
    def name(self) -> str: return f"Opening Range Breakout ({self.asset})"
    @property
    def asset(self) -> str: return self.params.get("_asset", "BTC")
    @property
    def strategy_type(self) -> str: return TYPE_NAME
    @property
    def default_params(self) -> dict:
        return {"range_bars": 4, "leverage": 3.0}
    @property
    def compatible_regimes(self) -> set[str]:
        return {"VOLATILE", "TREND_UP"}
    def describe(self) -> str:
        return "Trades the breakout of the high/low established in the first N bars of the session."
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        close = df["close"]
        curr_close = float(close.iloc[-1])
        
        n = self.params["range_bars"]
        recent_high = float(df["high"].rolling(n).max().iloc[-2])
        recent_low = float(df["low"].rolling(n).min().iloc[-2])
        
        entry = curr_close > recent_high
        exit_ = curr_close < recent_low
        
        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction="long", confidence=1.0,
            indicators={"orb_high": round(recent_high, 4)}
        )
    def parameter_space(self) -> dict:
        return {"range_bars": (2, 10, 2)}

STRATEGY_CLASS = ORBStrategy
STRATEGIES = [("TOMB-ORB", ORBStrategy, {"_asset": "BTC"})]
