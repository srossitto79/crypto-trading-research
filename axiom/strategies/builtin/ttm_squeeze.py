"""TTM Squeeze strategy.

Entry: Bollinger Bands contract inside Keltner Channels (squeeze on),
       then squeeze releases and momentum turns positive.
Exit: Momentum turns negative after being positive.
Compatible Regimes: BREAKOUT, TREND_UP
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "ttm_squeeze"


class TTMSqueezeStrategy(BaseStrategy):
    """TTM Squeeze momentum breakout strategy.

    Squeeze is detected when Bollinger Bands fit entirely inside
    Keltner Channels.  Entry triggers on the bar where the squeeze
    releases (bands expand outside KC) and the linear-regression
    momentum oscillator is positive and rising.
    """

    @property
    def name(self) -> str:
        return (
            f"TTMSqueeze(BB{self.params.get('bb_period', 20)}, "
            f"KC{self.params.get('kc_period', 20)}) ({self.asset})"
        )

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "bb_period": 20,
            "bb_std": 2.0,
            "kc_period": 20,
            "kc_mult": 1.5,
            "mom_period": 12,
            "leverage": 1.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"BREAKOUT", "TREND_UP"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Squeeze detected when BB({p['bb_period']}, {p['bb_std']}std) fits "
            f"inside KC({p['kc_period']}, {p['kc_mult']}x ATR). "
            f"Enters on squeeze release with positive momentum({p['mom_period']}). "
            f"Exits when momentum turns negative."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        bb_period = p["bb_period"]
        bb_std_mult = p["bb_std"]
        kc_period = p["kc_period"]
        kc_mult = p["kc_mult"]
        mom_period = p["mom_period"]
        close = df["close"]
        high = df["high"]
        low = df["low"]

        min_len = max(bb_period, kc_period, mom_period) + 2
        if len(df) < min_len:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=float(close.iloc[-1]),
                direction="long",
                confidence=0.0,
                indicators={},
            )

        # --- Bollinger Bands ---
        bb_ma = close.rolling(window=bb_period).mean()
        bb_sd = close.rolling(window=bb_period).std(ddof=0)
        bb_upper = bb_ma + bb_std_mult * bb_sd
        bb_lower = bb_ma - bb_std_mult * bb_sd

        # --- Keltner Channels (EMA + ATR) ---
        kc_ma = close.ewm(span=kc_period, adjust=False).mean()
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=kc_period).mean()
        kc_upper = kc_ma + kc_mult * atr
        kc_lower = kc_ma - kc_mult * atr

        # --- Squeeze detection ---
        squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

        # --- Momentum oscillator ---
        # close minus the average of (midline of highest-high/lowest-low, BB-MA)
        midline = (
            high.rolling(mom_period).max() + low.rolling(mom_period).min()
        ) / 2.0
        momentum = close - (midline + bb_ma) / 2.0

        curr_squeeze = bool(squeeze_on.iloc[-1])
        prev_squeeze = bool(squeeze_on.iloc[-2])
        curr_mom = float(momentum.iloc[-1])
        prev_mom = float(momentum.iloc[-2])
        curr_close = float(close.iloc[-1])

        # Entry: squeeze just released AND momentum positive
        squeeze_released = prev_squeeze and not curr_squeeze
        entry = squeeze_released and curr_mom > 0

        # Exit: momentum crosses from positive to negative
        exit_ = prev_mom > 0 and curr_mom <= 0

        direction = "long" if curr_mom > 0 else "short"

        confidence = 0.0
        if entry:
            mom_std = float(momentum.iloc[-bb_period:].std())
            if mom_std > 0:
                confidence = min(1.0, abs(curr_mom) / (2.0 * mom_std))

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(max(0.0, confidence), 4),
            indicators={
                "squeeze_on": curr_squeeze,
                "momentum": round(curr_mom, 4),
                "prev_momentum": round(prev_mom, 4),
                "bb_upper": round(float(bb_upper.iloc[-1]), 4),
                "bb_lower": round(float(bb_lower.iloc[-1]), 4),
                "kc_upper": round(float(kc_upper.iloc[-1]), 4),
                "kc_lower": round(float(kc_lower.iloc[-1]), 4),
            },
        )

    def parameter_space(self) -> dict:
        return {
            "bb_period": (15, 25, 5),
            "bb_std": (1.5, 2.5, 0.5),
            "kc_period": (15, 25, 5),
            "kc_mult": (1.0, 2.0, 0.5),
            "mom_period": (8, 16, 4),
        }


STRATEGY_CLASS = TTMSqueezeStrategy

STRATEGIES = [
    ("PREBUILT-TTM-SQUEEZE", TTMSqueezeStrategy, {"_asset": "BTC"}),
]
