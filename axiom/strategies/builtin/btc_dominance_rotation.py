"""BTC Dominance Rotation strategy — S084."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "btc_dominance_rotation"


class BtcDominanceRotationStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"BTC Dominance Rotation ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "dom_lookback_days": 7,
            "rsi_period": 14,
            "volume_sma": 20,
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
            f"Rotates based on BTC dominance trend. Rising dominance "
            f"(slope >0 over {p.get('dom_lookback_days', 7)}d) favors BTC trades. "
            f"Falling dominance favors alt trades. RSI + volume confirmation."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        rsi_period = int(p.get("rsi_period", 14))
        dom_lookback = int(p.get("dom_lookback_days", 7))
        vol_sma_period = int(p.get("volume_sma", 20))

        # Compute RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - 100 / (1 + rs)
        curr_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

        # Volume confirmation
        vol_ok = False
        if "volume" in df.columns:
            vol_sma = df["volume"].rolling(vol_sma_period).mean()
            curr_vol = float(df["volume"].iloc[-1])
            curr_vol_sma = float(vol_sma.iloc[-1]) if pd.notna(vol_sma.iloc[-1]) else 0
            vol_ok = curr_vol > curr_vol_sma if curr_vol_sma > 0 else False

        if "btc_dominance" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"btc_dominance_slope": 0, "rsi": round(curr_rsi, 2), "vol_ok": vol_ok},
            )

        dom = df["btc_dominance"]
        # Compute slope over lookback
        dom_slope = 0.0
        if len(dom.dropna()) > dom_lookback:
            dom_slope = float(dom.iloc[-1]) - float(dom.iloc[-dom_lookback])

        rising_dominance = dom_slope > 0
        asset = self.asset

        rsi_oversold = float(p.get("rsi_oversold", 30))
        rsi_overbought = float(p.get("rsi_overbought", 70))

        entry = False
        direction = "long"
        confidence = 0.0

        # Rising dominance -> only trade BTC; Falling -> trade alts (ETH)
        should_trade = (rising_dominance and asset == "BTC") or (not rising_dominance and asset != "BTC")

        if should_trade and vol_ok:
            if curr_rsi < rsi_oversold:
                entry = True
                direction = "long"
                confidence = min((rsi_oversold - curr_rsi) / rsi_oversold, 1.0)
            elif curr_rsi > rsi_overbought:
                entry = True
                direction = "short"
                confidence = min((curr_rsi - rsi_overbought) / (100 - rsi_overbought), 1.0)

        # Exit when RSI normalizes
        exit_signal = 40 < curr_rsi < 60

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "btc_dominance_slope": round(dom_slope, 4),
                "rsi": round(curr_rsi, 2),
                "vol_ok": bool(vol_ok),
                "should_trade": bool(should_trade),
                "dominance_rising": bool(rising_dominance),
            },
        )


STRATEGY_CLASS = BtcDominanceRotationStrategy

STRATEGIES = [
    ("S084-BTCDOM-BTC", BtcDominanceRotationStrategy, {"_asset": "BTC"}),
    ("S085-BTCDOM-ETH", BtcDominanceRotationStrategy, {"_asset": "ETH"}),
]
