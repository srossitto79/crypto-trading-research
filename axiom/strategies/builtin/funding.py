"""Funding Rate Mean Reversion strategy — S027."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "funding"


class FundingStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Funding Rate Mean Reversion ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "entry_threshold": 0.00003, "exit_threshold": 0.00001,
            "regime_ema200": True, "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}

    def describe(self) -> str:
        p = self.params
        threshold_pct = p.get("entry_threshold", 0.00003) * 100
        return (
            f"Buys when crypto futures funding becomes extremely negative "
            f"(shorts overpaying longs, below -{threshold_pct:.4f}%). "
            f"Exits when funding normalizes."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Funding rate mean reversion - uses df['funding_rate'] if available (backtest) or fetches live."""
        from axiom.scanner import atr

        p = self.params
        close = df["close"]
        ema200 = close.ewm(span=200, adjust=False).mean()
        atr_14 = atr(df, 14)
        
        curr_close = float(close.iloc[-1])
        curr_ema200 = float(ema200.iloc[-1])
        curr_atr = float(atr_14.iloc[-1])
        
        # Use pre-computed funding_rate from dataframe if available (backtest mode)
        # Otherwise fall back to live funding rate (live trading mode)
        funding = None
        if "funding_rate" in df.columns:
            # Backtest mode - use pre-computed funding rates
            funding = float(df["funding_rate"].iloc[-1])
        else:
            # Live mode - try to fetch live funding rate
            try:
                from axiom.strategies.sentiment import fetch_funding_rates
                funding_map = fetch_funding_rates()
                if isinstance(funding_map, dict):
                    funding_payload = funding_map.get(self.asset)
                    if isinstance(funding_payload, dict) and "funding" in funding_payload:
                        funding = float(funding_payload.get("funding", 0.0))
            except Exception:
                pass
        
        # If no funding data available, return neutral signal
        if funding is None:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"funding": 0, "ema200": round(curr_ema200, 4), "atr_14": round(curr_atr, 6), "adx": 0},
            )
        
        regime_ok = curr_close > curr_ema200

        entry_threshold = p.get("entry_threshold", 0.00003)
        exit_threshold = p.get("exit_threshold", 0.00001)

        entry = funding < -entry_threshold and regime_ok
        exit_ = funding > -exit_threshold

        return Signal(
            entry_signal=bool(entry), exit_signal=bool(exit_),
            price=round(curr_close, 4), direction="long",
            indicators={
                "funding": funding,
                "ema200": round(curr_ema200, 4),
                "atr_14": round(curr_atr, 6),
                "adx": 0,
                "regime_ok": bool(regime_ok),
            },
        )


STRATEGY_CLASS = FundingStrategy

STRATEGIES = [
    ("S027-FUND-BTC", FundingStrategy, {"_asset": "BTC"}),
]
