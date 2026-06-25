"""Stochastic Oscillator strategy — both long and short signals."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal
from axiom.scanner import atr, stochastic

TYPE_NAME = "stochastic"


class StochasticStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        d = self.params.get("direction", "long")
        return f"Stochastic {d.upper()} ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "k_period": 14,
            "d_period": 3,
            "k_oversold": 20,
            "k_overbought": 80,
            "k_exit_oversold": 40,
            "k_exit_overbought": 60,
            "direction": "long",
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN", "RANGE_BOUND"}

    def describe(self) -> str:
        p = self.params
        direction = p.get("direction", "long")
        if direction == "long":
            return (
                f"Buys when the {p['k_period']}-period Stochastic bounces from "
                f"oversold (below {p['k_oversold']}). "
                f"Sells at overbought (above {p['k_overbought']})."
            )
        return (
            f"Shorts when the {p['k_period']}-period Stochastic drops from "
            f"overbought (above {p['k_overbought']}). "
            f"Covers at oversold (below {p['k_oversold']})."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        
        stoch = stochastic(df, int(p.get("k_period", 14)), int(p.get("d_period", 3)))
        stoch_k = stoch["stoch_k"]
        stoch_d = stoch["stoch_d"]
        atr_14 = atr(df, 14)
        
        if len(df) < 2:
            return Signal(
                entry_signal=False, exit_signal=False,
                price=round(float(df["close"].iloc[-1]), 4),
                direction=p.get("direction", "long"),
                confidence=0.0, indicators={}
            )
        
        curr_close = float(df["close"].iloc[-1])
        curr_stoch_k = float(stoch_k.iloc[-1])
        prev_stoch_k = float(stoch_k.iloc[-2])
        curr_stoch_d = float(stoch_d.iloc[-1])
        curr_atr = float(atr_14.iloc[-1])
        
        direction = p.get("direction", "long")
        k_oversold = float(p.get("k_oversold", 20))
        k_overbought = float(p.get("k_overbought", 80))
        k_exit_oversold = float(p.get("k_exit_oversold", 40))
        k_exit_overbought = float(p.get("k_exit_overbought", 60))
        
        if direction == "long":
            entry = prev_stoch_k < k_oversold and curr_stoch_k >= k_oversold
            exit_ = curr_stoch_k >= k_overbought or (prev_stoch_k >= k_exit_oversold and curr_stoch_k < k_exit_oversold)
        else:
            entry = prev_stoch_k > k_overbought and curr_stoch_k <= k_overbought
            exit_ = curr_stoch_k <= k_oversold or (prev_stoch_k <= k_exit_overbought and curr_stoch_k > k_exit_overbought)
        
        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction=direction,
            confidence=min(1.0, abs(curr_stoch_k - curr_stoch_d) / 20) if entry else 0.0,
            indicators={
                "stoch_k": round(curr_stoch_k, 1),
                "stoch_d": round(curr_stoch_d, 1),
                "atr_14": round(curr_atr, 6),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "k_oversold": [10, 30, 5],
            "k_overbought": [70, 90, 5],
            "k_period": [10, 20, 2],
        }


STRATEGY_CLASS = StochasticStrategy

STRATEGIES = [
    ["S020-BTC-LONG", StochasticStrategy, {"_asset": "BTC", "direction": "long"}],
    ["S020-BTC-SHORT", StochasticStrategy, {"_asset": "BTC", "direction": "short"}],
    ["S020-ETH-LONG", StochasticStrategy, {"_asset": "ETH", "direction": "long"}],
    ["S020-ETH-SHORT", StochasticStrategy, {"_asset": "ETH", "direction": "short"}],
    ["S020-SOL-LONG", StochasticStrategy, {"_asset": "SOL", "direction": "long"}],
    ["S020-SOL-SHORT", StochasticStrategy, {"_asset": "SOL", "direction": "short"}],
]
