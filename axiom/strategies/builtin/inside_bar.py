"""Inside Bar Breakout strategy."""
import pandas as pd
from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "inside_bar"

class InsideBarStrategy(BaseStrategy):
    @property
    def name(self) -> str: return f"Inside Bar Breakout ({self.asset})"
    @property
    def asset(self) -> str: return self.params.get("_asset", "BTC")
    @property
    def strategy_type(self) -> str: return TYPE_NAME
    @property
    def default_params(self) -> dict:
        return { "leverage": 3.0}
    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}
    def describe(self) -> str:
        return "Pure price action: enters on the breakout of an inside bar pattern."
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        curr_close = float(df["close"].iloc[-1])
        high_1, high_2 = df["high"].iloc[-2], df["high"].iloc[-3]
        low_1, low_2 = df["low"].iloc[-2], df["low"].iloc[-3]
        
        is_inside_bar = (high_1 < high_2) and (low_1 > low_2)
        breakout_up = is_inside_bar and (curr_close > high_1)
        
        entry = breakout_up
        exit_ = curr_close < low_1
        
        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction="long", confidence=1.0,
            indicators={"inside_bar": bool(is_inside_bar)}
        )
    def parameter_space(self) -> dict:
        return {}

STRATEGY_CLASS = InsideBarStrategy
STRATEGIES = [("TOMB-INSIDEBAR", InsideBarStrategy, {"_asset": "BTC"})]
