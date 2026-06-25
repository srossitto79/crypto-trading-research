"""Z-score rebound strategy with EMA context."""

from __future__ import annotations

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "zscore_mean_reclaim"


class ZScoreMeanReclaimStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Z-Score Mean Reclaim ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "ETH")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "zscore_window": 20,
            "zscore_entry": -1.5,
            "zscore_exit": 0.3,
            "ema_period": 34,
            "leverage": 1.5,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "HIGH_VOL"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Fade a washout when z-score({p['zscore_window']}) rebounds through {p['zscore_entry']} "
            f"and price is reclaiming the short-term mean; exit near {p['zscore_exit']}."
        )

    def _indicator_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        rolling_mean = close.rolling(int(self.params["zscore_window"])).mean()
        rolling_std = close.rolling(int(self.params["zscore_window"])).std()
        zscore = (close - rolling_mean) / rolling_std
        ema = close.ewm(span=int(self.params["ema_period"]), adjust=False).mean()

        return pd.DataFrame(
            {"close": close, "rolling_mean": rolling_mean, "rolling_std": rolling_std, "zscore": zscore, "ema": ema},
            index=df.index,
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        indicators = self._indicator_frame(df)
        current = indicators.iloc[-1]
        previous = indicators.iloc[-2] if len(indicators) > 1 else current

        reclaim = bool(current["close"] >= previous["close"])
        entry = bool(previous["zscore"] < float(self.params["zscore_entry"]) and current["zscore"] >= float(self.params["zscore_entry"]) and reclaim)
        exit_ = bool(current["zscore"] >= float(self.params["zscore_exit"]) or current["close"] > current["ema"])

        return Signal(
            entry_signal=entry,
            exit_signal=exit_,
            price=round(float(current["close"]), 4),
            direction="long",
            confidence=round(min(1.0, abs(float(current["zscore"])) / 3.0) if pd.notna(current["zscore"]) else 0.0, 4),
            indicators={
                "zscore": round(float(current["zscore"]), 4) if pd.notna(current["zscore"]) else 0.0,
                "rolling_mean": round(float(current["rolling_mean"]), 4) if pd.notna(current["rolling_mean"]) else 0.0,
                "ema": round(float(current["ema"]), 4),
                "reclaim": reclaim,
            },
        )

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        indicators = self._indicator_frame(df)
        reclaim = indicators["close"] >= indicators["close"].shift(1)
        entry_signals = (indicators["zscore"].shift(1) < float(self.params["zscore_entry"])) & (
            indicators["zscore"] >= float(self.params["zscore_entry"])
        ) & reclaim
        exit_signals = (indicators["zscore"] >= float(self.params["zscore_exit"])) | (
            indicators["close"] > indicators["ema"]
        )
        return entry_signals.fillna(False), exit_signals.fillna(False)

    def parameter_space(self) -> dict:
        return {
            "zscore_window": (10, 40, 5),
            "zscore_entry": (-2.5, -1.0, 0.25),
            "zscore_exit": (-0.25, 0.75, 0.25),
        }


STRATEGY_CLASS = ZScoreMeanReclaimStrategy

STRATEGIES = [
    ("PREBUILT-ZSCORE-MEAN-RECLAIM-ETH", ZScoreMeanReclaimStrategy, {"_asset": "ETH"}),
]
