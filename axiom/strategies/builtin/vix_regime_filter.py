"""VIX Regime Filter strategy — S080."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "vix_regime_filter"


class VixRegimeFilterStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"VIX Regime Filter ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "vix_high": 25,
            "vix_low": 15,
            "rsi_period": 14,
            "ema_fast": 12,
            "ema_slow": 26,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"VIX-based regime filter. High VIX (>{p.get('vix_high', 25)}) uses "
            f"mean-reversion via RSI extremes. Low VIX (<{p.get('vix_low', 15)}) uses "
            f"trend-following via EMA crossover."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        rsi_period = int(p.get("rsi_period", 14))
        ema_fast_period = int(p.get("ema_fast", 12))
        ema_slow_period = int(p.get("ema_slow", 26))

        # Compute RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - 100 / (1 + rs)
        curr_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

        # Compute EMAs
        ema_fast = close.ewm(span=ema_fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=ema_slow_period, adjust=False).mean()
        curr_ema_fast = float(ema_fast.iloc[-1])
        curr_ema_slow = float(ema_slow.iloc[-1])

        if "vix_close" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"vix": 0, "rsi": round(curr_rsi, 2), "ema_fast": round(curr_ema_fast, 4), "ema_slow": round(curr_ema_slow, 4)},
            )

        curr_vix = float(df["vix_close"].iloc[-1]) if pd.notna(df["vix_close"].iloc[-1]) else 20.0
        vix_high = float(p.get("vix_high", 25))
        vix_low = float(p.get("vix_low", 15))
        rsi_oversold = float(p.get("rsi_oversold", 30))
        rsi_overbought = float(p.get("rsi_overbought", 70))

        entry = False
        direction = "long"
        confidence = 0.0

        if curr_vix > vix_high:
            # High VIX regime -> mean-reversion with RSI
            if curr_rsi < rsi_oversold:
                entry = True
                direction = "long"
                confidence = min((rsi_oversold - curr_rsi) / rsi_oversold, 1.0)
            elif curr_rsi > rsi_overbought:
                entry = True
                direction = "short"
                confidence = min((curr_rsi - rsi_overbought) / (100 - rsi_overbought), 1.0)
        elif curr_vix < vix_low:
            # Low VIX regime -> trend-following with EMA cross
            if curr_ema_fast > curr_ema_slow:
                entry = True
                direction = "long"
                confidence = min((curr_ema_fast - curr_ema_slow) / curr_ema_slow * 100, 1.0)
            elif curr_ema_fast < curr_ema_slow:
                entry = True
                direction = "short"
                confidence = min((curr_ema_slow - curr_ema_fast) / curr_ema_slow * 100, 1.0)

        # Exit: VIX in neutral zone and no strong signal
        exit_signal = vix_low <= curr_vix <= vix_high

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "vix": round(curr_vix, 2),
                "rsi": round(curr_rsi, 2),
                "ema_fast": round(curr_ema_fast, 4),
                "ema_slow": round(curr_ema_slow, 4),
                "regime": "mean_reversion" if curr_vix > vix_high else ("trend" if curr_vix < vix_low else "neutral"),
            },
        )


STRATEGY_CLASS = VixRegimeFilterStrategy

STRATEGIES = [
    ("S080-VIX-BTC", VixRegimeFilterStrategy, {"_asset": "BTC"}),
    ("S081-VIX-ETH", VixRegimeFilterStrategy, {"_asset": "ETH"}),
]
