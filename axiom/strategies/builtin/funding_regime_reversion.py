"""Funding Rate Regime Reversion strategy — mean reversion on extreme funding cycles."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "funding_regime_reversion"


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI helper using Wilder smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


class FundingRegimeReversionStrategy(BaseStrategy):
    """Mean reversion strategy that trades extremes in the funding rate cycle.

    Hypothesis:
        When 8h funding rate is extremely negative → market is too bearish,
        expect a bounce (long entry). When extremely positive → too bullish,
        expect a pullback (short entry).

    Long entry (all must be true):
        1. funding_zscore < -2  (funding rate unusually low, longs being paid)
        2. rsi(14) < 40         (price in oversold zone)

    Short entry (all must be true):
        1. funding_zscore > 2   (funding rate unusually high, shorts being paid)
        2. rsi(14) > 60         (price in overbought zone)

    Exit: funding rate reverts to within ±0.5 Z-score, or RSI normalizes.

    Fallback: When funding_rate column is absent (no funding data), fall back to
    pure RSI mean reversion — same entry/exit thresholds applied to price action.
    """

    @property
    def name(self) -> str:
        return f"Funding Regime Reversion ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "funding_window": 24,
            "zscore_entry": 2.0,
            "zscore_exit": 0.5,
            "rsi_period": 14,
            "rsi_oversold": 40,
            "rsi_overbought": 60,
            "ema_period": 200,
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "VOLATILE"}

    def describe(self) -> str:
        return (
            "Takes long positions when the funding rate Z-score reaches extreme "
            "negative territory (undervalued longs), with RSI confirming oversold. "
            "Takes short positions when funding is extremely positive. "
            "Falls back to pure RSI mean reversion when funding data is unavailable."
        )

    def _compute_funding_zscore(self, df: pd.DataFrame, window: int) -> pd.Series | None:
        """Compute rolling Z-score of funding_rate. Returns None if column missing."""
        if "funding_rate" not in df.columns:
            return None
        fr = df["funding_rate"].dropna()
        if len(fr) < window:
            return None
        mean = fr.rolling(window=window, min_periods=1).mean()
        std = fr.rolling(window=window, min_periods=1).std().replace(0, 1e-10)
        return (fr - mean) / std

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params

        min_bars = max(p["funding_window"], p["rsi_period"], p["ema_period"])
        if len(df) < min_bars:
            return Signal(
                price=round(float(df["close"].iloc[-1]), 4),
                direction="long",
                indicators={"error": "insufficient_bars"},
            )

        close = df["close"]
        ema = close.ewm(span=p["ema_period"], adjust=False).mean()

        rsi_series = _rsi(close, p["rsi_period"])
        rsi_now = float(rsi_series.iloc[-1])

        price_now = float(close.iloc[-1])
        ema_now = float(ema.iloc[-1])

        # ── Funding Z-score path ──────────────────────────────────────────────
        zscore_series = self._compute_funding_zscore(df, p["funding_window"])

        # Default exit flags
        long_exit = False
        short_exit = False

        if zscore_series is not None and not zscore_series.isna().iloc[-1]:
            z_now = float(zscore_series.iloc[-1])

            long_entry = z_now < -p["zscore_entry"] and rsi_now < p["rsi_oversold"]
            short_entry = z_now > p["zscore_entry"] and rsi_now > p["rsi_overbought"]
            long_exit = z_now > -p["zscore_exit"] and rsi_now > p["rsi_oversold"]
            short_exit = z_now < p["zscore_exit"] and rsi_now < p["rsi_overbought"]

            if long_entry:
                return Signal(
                    entry_signal=True, exit_signal=False,
                    price=round(price_now, 4), direction="long",
                    confidence=1.0,
                    indicators={
                        "funding_zscore": round(z_now, 4),
                        "rsi": round(rsi_now, 1),
                        "ema_200": round(ema_now, 4),
                        "mode": "funding",
                    },
                )

            if short_entry:
                return Signal(
                    entry_signal=True, exit_signal=False,
                    price=round(price_now, 4), direction="short",
                    confidence=1.0,
                    indicators={
                        "funding_zscore": round(z_now, 4),
                        "rsi": round(rsi_now, 1),
                        "ema_200": round(ema_now, 4),
                        "mode": "funding",
                    },
                )

        else:
            # ── Fallback: RSI-only mean reversion ─────────────────────────────
            # Long: RSI at oversold and bouncing
            prev_rsi = float(rsi_series.iloc[-2])
            rsi_touch_oversold = prev_rsi < p["rsi_oversold"] and rsi_now > p["rsi_oversold"]
            rsi_touch_overbought = prev_rsi > p["rsi_overbought"] and rsi_now < p["rsi_overbought"]

            long_entry = rsi_touch_oversold and price_now > ema_now
            short_entry = rsi_touch_overbought and price_now < ema_now
            long_exit = rsi_now > p["rsi_oversold"] + 10
            short_exit = rsi_now < p["rsi_overbought"] - 10

            if long_entry:
                return Signal(
                    entry_signal=True, exit_signal=False,
                    price=round(price_now, 4), direction="long",
                    confidence=1.0,
                    indicators={
                        "rsi": round(rsi_now, 1),
                        "ema_200": round(ema_now, 4),
                        "mode": "rsi_fallback",
                    },
                )

            if short_entry:
                return Signal(
                    entry_signal=True, exit_signal=False,
                    price=round(price_now, 4), direction="short",
                    confidence=1.0,
                    indicators={
                        "rsi": round(rsi_now, 1),
                        "ema_200": round(ema_now, 4),
                        "mode": "rsi_fallback",
                    },
                )

        # Neutral
        return Signal(
            entry_signal=False,
            exit_signal=bool(long_exit or short_exit),
            price=round(price_now, 4),
            direction="long",
            confidence=0.0,
            indicators={
                "rsi": round(rsi_now, 1),
                "ema_200": round(ema_now, 4),
                "mode": "neutral",
            },
        )

    def parameter_space(self) -> dict:
        return {
            "zscore_entry": (1.5, 3.0, 0.5),
            "rsi_oversold": (30, 45, 5),
            "rsi_overbought": (55, 70, 5),
            "funding_window": (12, 48, 12),
        }


STRATEGY_CLASS = FundingRegimeReversionStrategy

STRATEGIES = [
    ("S-FRR-BTC", FundingRegimeReversionStrategy, {"_asset": "BTC"}),
    ("S-FRR-ETH", FundingRegimeReversionStrategy, {"_asset": "ETH"}),
]