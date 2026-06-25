"""Macro Regime Composite strategy — S086."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "macro_regime_composite"


class MacroRegimeCompositeStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Macro Regime Composite ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "vix_threshold": 20,
            "dxy_slope_period": 10,
            "risk_score_threshold": 0.6,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Master macro regime classifier combining VIX, DXY, and Treasury 10Y "
            f"into a risk score. Only enters when risk score exceeds "
            f"{p.get('risk_score_threshold', 0.6)} (risk-on = long, risk-off = short)."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        vix_threshold = float(p.get("vix_threshold", 20))
        dxy_period = int(p.get("dxy_slope_period", 10))
        risk_thresh = float(p.get("risk_score_threshold", 0.6))

        # We need at least one macro column
        has_vix = "vix_close" in df.columns
        has_dxy = "dxy_close" in df.columns
        has_treasury = "treasury_10y" in df.columns

        if not has_vix and not has_dxy and not has_treasury:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"risk_score": 0, "macro_signals": 0},
            )

        # Build risk score from -1 (risk-off) to +1 (risk-on)
        signals = []

        # VIX component: low VIX = risk-on (+1), high VIX = risk-off (-1)
        vix_score = 0.0
        if has_vix:
            curr_vix = float(df["vix_close"].iloc[-1]) if pd.notna(df["vix_close"].iloc[-1]) else 20.0
            if curr_vix < vix_threshold:
                vix_score = min((vix_threshold - curr_vix) / vix_threshold, 1.0)
            else:
                vix_score = -min((curr_vix - vix_threshold) / vix_threshold, 1.0)
            signals.append(vix_score)

        # DXY component: falling DXY = risk-on (+1), rising = risk-off (-1)
        dxy_score = 0.0
        if has_dxy:
            dxy = df["dxy_close"]
            dxy_sma = dxy.rolling(dxy_period).mean()
            if len(dxy_sma.dropna()) >= 2:
                slope = float(dxy_sma.iloc[-1]) - float(dxy_sma.iloc[-2])
                dxy_score = -1.0 if slope > 0 else 1.0 if slope < 0 else 0.0
            signals.append(dxy_score)

        # Treasury component: falling yields = risk-on (+1), rising = risk-off (-1)
        treasury_score = 0.0
        if has_treasury:
            ty = df["treasury_10y"]
            ty_sma = ty.rolling(dxy_period).mean()
            if len(ty_sma.dropna()) >= 2:
                slope = float(ty_sma.iloc[-1]) - float(ty_sma.iloc[-2])
                treasury_score = -1.0 if slope > 0 else 1.0 if slope < 0 else 0.0
            signals.append(treasury_score)

        # Composite risk score
        risk_score = sum(signals) / len(signals) if signals else 0.0

        entry = abs(risk_score) >= risk_thresh
        direction = "long" if risk_score > 0 else "short"
        confidence = min(abs(risk_score), 1.0)

        # Exit when risk score is near zero
        exit_signal = abs(risk_score) < 0.2

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "risk_score": round(risk_score, 4),
                "vix_score": round(vix_score, 4),
                "dxy_score": round(dxy_score, 4),
                "treasury_score": round(treasury_score, 4),
                "macro_signals": len(signals),
            },
        )


STRATEGY_CLASS = MacroRegimeCompositeStrategy

STRATEGIES = [
    ("S086-MACRO-BTC", MacroRegimeCompositeStrategy, {"_asset": "BTC"}),
    ("S087-MACRO-ETH", MacroRegimeCompositeStrategy, {"_asset": "ETH"}),
]
