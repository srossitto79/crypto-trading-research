"""PPO (Percentage Price Oscillator) strategy.

Entry: PPO crosses above signal line AND PPO > 0
Exit: PPO crosses below signal line
Compatible Regimes: TREND_UP, TREND_DOWN
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "ppo"


class PPOStrategy(BaseStrategy):
    """Percentage Price Oscillator trend-following strategy.

    PPO = (EMA_fast - EMA_slow) / EMA_slow * 100
    Signal = EMA(PPO, signal_period)
    Entry when PPO crosses above signal and PPO > 0; exit when PPO crosses
    below signal.
    """

    @property
    def name(self) -> str:
        p = self.params
        return f"PPO({p.get('fast', 12)}/{p.get('slow', 26)}/{p.get('signal', 9)}) ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "fast": 12,
            "slow": 26,
            "signal": 9,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Enters when PPO({p['fast']}/{p['slow']}) crosses above its "
            f"{p['signal']}-bar signal line while PPO > 0. "
            f"Exits when PPO crosses below signal."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        slow = p["slow"]

        if len(df) < slow + p["signal"] + 2:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        ema_fast = close.ewm(span=p["fast"], adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        ppo = (ema_fast - ema_slow) / ema_slow * 100.0
        signal_line = ppo.ewm(span=p["signal"], adjust=False).mean()

        curr_ppo = float(ppo.iloc[-1])
        prev_ppo = float(ppo.iloc[-2])
        curr_sig = float(signal_line.iloc[-1])
        prev_sig = float(signal_line.iloc[-2])
        curr_close = float(close.iloc[-1])

        cross_above = prev_ppo <= prev_sig and curr_ppo > curr_sig
        cross_below = prev_ppo >= prev_sig and curr_ppo < curr_sig

        entry = cross_above and curr_ppo > 0
        exit_ = cross_below

        confidence = 0.0
        if entry:
            confidence = min(1.0, abs(curr_ppo - curr_sig) / 1.0)

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=confidence,
            indicators={
                "ppo": round(curr_ppo, 4),
                "ppo_signal": round(curr_sig, 4),
                "ppo_hist": round(curr_ppo - curr_sig, 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "fast": (8, 16, 2),
            "slow": (20, 30, 2),
            "signal": (5, 12, 1),
        }


STRATEGY_CLASS = PPOStrategy

STRATEGIES = [
    ("PREBUILT-PPO", PPOStrategy, {"_asset": "BTC"}),
]
