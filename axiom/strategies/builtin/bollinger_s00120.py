"""Bollinger Band Breakout with RSI Filter — S00120.

Strategy: BNB-BOLLINGER-S00120
Entry: Price breaks above upper Bollinger Band (20-period, 2 std) AND RSI < rsi_entry_long (oversold)
Exit: Price falls below middle Bollinger Band OR RSI > rsi_entry_short (overbought)
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "bollinger"


class BollingerS00120Strategy(BaseStrategy):
    """S00120: Bollinger Band Breakout with RSI Filter for BNB."""

    @property
    def name(self) -> str:
        return f"Bollinger RSI Filter ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BNB")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_entry_long": 30,
            "rsi_entry_short": 70,
            "rsi_period": 14,
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "RANGE_BOUND", "VOLATILE"}

    def describe(self) -> str:
        p = self.params
        return (
            f"BNB S00120: Buys when price breaks above the upper Bollinger Band "
            f"({p['bb_period']}-period, {p['bb_std']} std dev) while RSI < {p['rsi_entry_long']} "
            f"(oversold at breakout). Exits when price falls below middle band "
            f"or RSI > {p['rsi_entry_short']}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        from axiom.scanner import rsi as compute_rsi, atr
        
        p = self.params
        bp = p.get("bb_period", 20)
        rsi_period = p.get("rsi_period", 14)
        rsi_entry_long = p.get("rsi_entry_long", 30)
        rsi_entry_short = p.get("rsi_entry_short", 70)
        
        close = df["close"]
        
        # Bollinger Bands
        bb_mid = close.rolling(bp).mean()
        bb_std = close.rolling(bp).std()
        bb_upper = bb_mid + p.get("bb_std", 2.0) * bb_std
        bb_lower = bb_mid - p.get("bb_std", 2.0) * bb_std
        
        # RSI
        rsi_val = compute_rsi(close, rsi_period)
        
        # ATR for position sizing
        atr_val = atr(df, 14)

        # Get values for current and previous bar
        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        
        curr_bb_upper = float(bb_upper.iloc[-1])
        prev_bb_upper = float(bb_upper.iloc[-2])
        curr_bb_mid = float(bb_mid.iloc[-1])
        curr_bb_lower = float(bb_lower.iloc[-1])
        
        curr_rsi = float(rsi_val.iloc[-1])
        prev_rsi = float(rsi_val.iloc[-2])
        
        curr_atr = float(atr_val.iloc[-1])

        # Entry: breakout above upper band AND RSI oversold (catching the bounce)
        # Also allow entry if RSI was above entry threshold and now below (RSI reversal)
        breakout = prev_close <= prev_bb_upper and curr_close > curr_bb_upper
        rsi_oversold = curr_rsi < rsi_entry_long
        rsi_reversal = prev_rsi >= rsi_entry_long and curr_rsi < rsi_entry_long
        
        entry = breakout and (rsi_oversold or rsi_reversal)
        
        # Exit: price below middle band OR RSI overbought
        exit_price = curr_close < curr_bb_mid
        exit_rsi = curr_rsi > rsi_entry_short
        
        exit_signal = exit_price or exit_rsi

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction="long",
            confidence=min(1.0, (curr_rsi / 100)) if entry else 0.0,
            indicators={
                "bb_mid": round(curr_bb_mid, 4),
                "bb_upper": round(curr_bb_upper, 4),
                "bb_lower": round(curr_bb_lower, 4),
                "rsi": round(curr_rsi, 1),
                "atr_14": round(curr_atr, 6),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "bb_period": (15, 30, 5),
            "bb_std": (1.5, 3.0, 0.5),
            "rsi_entry_long": (20, 40, 5),
            "rsi_entry_short": (60, 80, 5),
            "rsi_period": (10, 20, 2),
        }


STRATEGY_CLASS = BollingerS00120Strategy

STRATEGIES = [
    ("S00120-BNB-BOLLINGER", BollingerS00120Strategy, {"_asset": "BNB"}),
]
