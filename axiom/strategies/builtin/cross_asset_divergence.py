"""Cross-Asset Divergence Mean Reversion strategy — S088."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "cross_asset_divergence"


class CrossAssetDivergenceStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Cross-Asset Divergence ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "correlation_window": 30,
            "divergence_zscore": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Mean-reversion on cross-asset divergence. Computes rolling Z-score of "
            f"asset_return minus spy_return over {p.get('correlation_window', 30)} bars. "
            f"Fades when divergence exceeds {p.get('divergence_zscore', 2.0)}\u03c3."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        window = int(p.get("correlation_window", 30))
        div_thresh = float(p.get("divergence_zscore", 2.0))
        rsi_period = int(p.get("rsi_period", 14))

        # Compute RSI for confirmation
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - 100 / (1 + rs)
        curr_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

        if "spy_close" not in df.columns:
            return Signal(
                price=round(curr_close, 4),
                direction="long",
                indicators={"divergence_zscore": 0, "rsi": round(curr_rsi, 2)},
            )

        # Compute returns
        asset_ret = close.pct_change()
        spy_ret = df["spy_close"].pct_change()

        # Return spread
        spread = asset_ret - spy_ret
        rolling_mean = spread.rolling(window).mean()
        rolling_std = spread.rolling(window).std()
        z = (spread - rolling_mean) / rolling_std.replace(0, float("nan"))

        curr_z = float(z.iloc[-1]) if pd.notna(z.iloc[-1]) else 0.0

        rsi_oversold = float(p.get("rsi_oversold", 30))
        rsi_overbought = float(p.get("rsi_overbought", 70))

        entry = False
        direction = "long"
        confidence = 0.0

        if curr_z > div_thresh:
            # Asset outperformed SPY too much -> fade with short
            if curr_rsi > rsi_overbought:
                entry = True
                direction = "short"
                confidence = min(abs(curr_z) / (div_thresh * 2), 1.0)
        elif curr_z < -div_thresh:
            # Asset underperformed SPY too much -> fade with long
            if curr_rsi < rsi_oversold:
                entry = True
                direction = "long"
                confidence = min(abs(curr_z) / (div_thresh * 2), 1.0)

        # Exit when Z-score normalizes
        exit_signal = abs(curr_z) < 0.5

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "divergence_zscore": round(curr_z, 4),
                "rsi": round(curr_rsi, 2),
            },
        )


STRATEGY_CLASS = CrossAssetDivergenceStrategy

STRATEGIES = [
    ("S088-XDIV-BTC", CrossAssetDivergenceStrategy, {"_asset": "BTC"}),
    ("S089-XDIV-ETH", CrossAssetDivergenceStrategy, {"_asset": "ETH"}),
]
