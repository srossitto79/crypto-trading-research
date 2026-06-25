"""Keltner Channel Breakout strategy - S025, S00019, S00027 variants.

Supports both LONG and SHORT position modes.
"""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "keltner"


class KeltnerStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Keltner Channel Breakout ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "ETH")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "kc_period": 20, "kc_mult": 2.0,
            "adx_period": 14, "adx_min": 20, "leverage": 3.0,
            "position": "long",  # "long" or "short"
        }

    @property
    def compatible_regimes(self) -> set[str]:
        position = self.params.get("position", "long")
        if position == "short":
            return {"TREND_DOWN"}
        return {"TREND_UP"}

    def describe(self) -> str:
        p = self.params
        kp = p.get("keltner_period") or p.get("keltner_window") or p.get("kc_period", 20)
        km = p.get("keltner_mult") or p.get("keltner_multiplier") or p.get("kc_mult", 2.0)
        position = p.get("position", "long")
        
        if position == "short":
            return (
                f"Shorts when price breaks below the lower Keltner Channel "
                f"({kp}-period, {km}x ATR) in a downtrend. "
                f"Covers when price rises to the middle line."
            )
        return (
            f"Buys when price breaks above the upper Keltner Channel "
            f"({kp}-period, {km}x ATR) in an uptrend. "
            f"Sells when price falls to the middle line."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        from axiom.scanner import adx
        p = self.params
        
        # Support multiple naming conventions
        kp = (
            p.get("keltner_period") or 
            p.get("keltner_window") or 
            p.get("kc_period", 20)
        )
        km = (
            p.get("keltner_mult") or 
            p.get("keltner_multiplier") or 
            p.get("kc_mult", 2.0)
        )
        # Also support atr_multiplier as alias for keltner multiplier
        if p.get("atr_multiplier"):
            km = p.get("atr_multiplier")
            
        adx_period = p.get("adx_period", 14)
        adx_min = p.get("adx_min", 20)
        position = p.get("position", "long")  # "long" or "short"
        
        close = df["close"]
        kc_mid = close.ewm(span=kp, adjust=False).mean()
        h, low_p, c = df["high"], df["low"], df["close"]
        tr = pd.concat([(h - low_p), (h - c.shift()).abs(), (low_p - c.shift()).abs()], axis=1).max(axis=1)
        atr_kc = tr.ewm(span=kp, adjust=False).mean()
        kc_upper = kc_mid + km * atr_kc
        kc_lower = kc_mid - km * atr_kc
        
        # Optional: ADX filter for regime detection
        use_adx_filter = p.get("use_adx_filter", True)
        if use_adx_filter:
            adx_val = adx(df, adx_period)
            curr_adx = float(adx_val.iloc[-1])
        else:
            curr_adx = 50  # Default to allowing signals if ADX filter disabled
            
        # Check if we have enough data
        if len(df) < kp + 2:
            return Signal.HOLD
            
        prev_close = close.iloc[-2]
        curr_close = close.iloc[-1]
        prev_upper = kc_upper.iloc[-2]
        curr_upper = kc_upper.iloc[-1]
        prev_lower = kc_lower.iloc[-2]
        curr_lower = kc_lower.iloc[-1]
        curr_mid = kc_mid.iloc[-1]
        
        if position == "short":
            # SHORT SIGNAL: price breaks below lower Keltner channel in downtrend
            if curr_close < curr_lower and prev_close >= prev_lower:
                if curr_adx >= adx_min:
                    return Signal(
                        entry_signal=True,
                        exit_signal=False,
                        price=float(curr_close),
                        direction="short",
                        confidence=0.7,
                        indicators={"kc_upper": float(kc_upper.iloc[-1]), "kc_lower": float(kc_lower.iloc[-1]), "kc_mid": float(curr_mid), "adx": curr_adx}
                    )
            
            # EXIT SHORT: price rises above middle line
            if curr_close > curr_mid:
                return Signal(
                    entry_signal=False,
                    exit_signal=True,
                    price=float(curr_close),
                    direction="short",
                    confidence=1.0,
                    indicators={"kc_mid": float(curr_mid)}
                )
        else:
            # LONG SIGNAL: price breaks above upper Keltner channel in uptrend
            if curr_close > curr_upper and prev_close <= prev_upper:
                if curr_adx >= adx_min:
                    return Signal(
                        entry_signal=True,
                        exit_signal=False,
                        price=float(curr_close),
                        direction="long",
                        confidence=0.7,
                        indicators={"kc_upper": float(kc_upper.iloc[-1]), "kc_lower": float(kc_lower.iloc[-1]), "kc_mid": float(curr_mid), "adx": curr_adx}
                    )
            
            # EXIT LONG: price falls below middle line
            if curr_close < curr_mid:
                return Signal(
                    entry_signal=False,
                    exit_signal=True,
                    price=float(curr_close),
                    direction="long",
                    confidence=1.0,
                    indicators={"kc_mid": float(curr_mid)}
                )
            
        return Signal.HOLD


STRATEGY_CLASS = KeltnerStrategy
