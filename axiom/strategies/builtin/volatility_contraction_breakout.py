"""Volatility Contraction Breakout strategy — trades squeeze expansions."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "volatility_contraction_breakout"


def _atr(series: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range helper (uses high/low/close of the current row only)."""
    tr = series.diff().abs()
    tr = tr.where(tr > 0, 0.0)
    return tr.rolling(window=period, min_periods=1).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI helper using Wilder smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


class VolatilityContractionBreakoutStrategy(BaseStrategy):
    """Volatility contraction breakout with ATR squeeze + Bollinger Band width detection.

    Long entry rules (all must be true):
        1. ATR(14) < 20th percentile of ATR(14) over lookback window  → squeeze state
        2. BB width < 0.5 * BB width median over lookback              → very tight bands
        3. close > EMA(period)                                        → uptrend filter
        4. RSI(14) > 50                                              → momentum confirmation

    Short entry rules (all must be true):
        1. ATR(14) < 20th percentile (squeeze)
        2. BB width < 0.5 * BB width median
        3. close < EMA(period)                                        → downtrend
        4. RSI(14) < 50                                              → weak momentum

    Exit: price crosses EMA from the entry direction, or ATR resumes above median.
    """

    @property
    def name(self) -> str:
        return f"Volatility Contraction Breakout ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "atr_period": 14,
            "bb_period": 20,
            "bb_std": 2.0,
            "ema_period": 200,
            "rsi_period": 14,
            "atr_percentile": 20,
            "bb_width_multiplier": 0.5,
            "lookback": 200,
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        return (
            "Trades the expansion phase after a volatility squeeze. "
            "Long when ATR and Bollinger Band width contract below threshold "
            "while price holds above EMA200 and RSI confirms momentum. "
            "Short mirrors the logic for downtrend environments."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params

        if len(df) < max(p["lookback"], p["bb_period"], p["ema_period"], p["rsi_period"]):
            # Not enough bars for reliable computation
            return Signal(
                price=round(float(df["close"].iloc[-1]), 4),
                direction="long",
                indicators={"error": "insufficient_bars"},
            )

        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ── ATR ────────────────────────────────────────────────────────────────
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr_series = tr.rolling(window=p["atr_period"], min_periods=1).mean()
        atr_now = float(atr_series.iloc[-1])

        # ── Bollinger Bands ────────────────────────────────────────────────────
        bb_mid = close.rolling(window=p["bb_period"]).mean()
        bb_std = close.rolling(window=p["bb_period"]).std()
        bb_upper = bb_mid + p["bb_std"] * bb_std
        bb_lower = bb_mid - p["bb_std"] * bb_std
        bb_width = bb_upper - bb_lower

        # ── EMA ───────────────────────────────────────────────────────────────
        ema = close.ewm(span=p["ema_period"], adjust=False).mean()

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi_now = float(_rsi(close, p["rsi_period"]).iloc[-1])

        # ── Squeeze thresholds ─────────────────────────────────────────────────
        lb = p["lookback"]
        atr_percentile_val = atr_series.iloc[-lb:].quantile(p["atr_percentile"] / 100.0)
        bb_width_median = bb_width.iloc[-lb:].median()

        is_squeezed = atr_now < atr_percentile_val and float(bb_width.iloc[-1]) < p["bb_width_multiplier"] * bb_width_median

        price_now = float(close.iloc[-1])
        ema_now = float(ema.iloc[-1])

        # ── Entry logic ────────────────────────────────────────────────────────
        long_entry = is_squeezed and price_now > ema_now and rsi_now > 50
        short_entry = is_squeezed and price_now < ema_now and rsi_now < 50

        # ── Exit logic ─────────────────────────────────────────────────────────
        # Exit long: price drops below EMA or squeeze ends
        long_exit = price_now < ema_now
        # Exit short: price rises above EMA or squeeze ends
        short_exit = price_now > ema_now

        if long_entry:
            return Signal(
                entry_signal=True,
                exit_signal=False,
                price=round(price_now, 4),
                direction="long",
                confidence=1.0,
                indicators={
                    "atr_14": round(atr_now, 6),
                    "bb_width": round(float(bb_width.iloc[-1]), 4),
                    "bb_width_median": round(bb_width_median, 4),
                    "ema_200": round(ema_now, 4),
                    "rsi": round(rsi_now, 1),
                    "squeezed": is_squeezed,
                },
            )

        if short_entry:
            return Signal(
                entry_signal=True,
                exit_signal=False,
                price=round(price_now, 4),
                direction="short",
                confidence=1.0,
                indicators={
                    "atr_14": round(atr_now, 6),
                    "bb_width": round(float(bb_width.iloc[-1]), 4),
                    "bb_width_median": round(bb_width_median, 4),
                    "ema_200": round(ema_now, 4),
                    "rsi": round(rsi_now, 1),
                    "squeezed": is_squeezed,
                },
            )

        return Signal(
            entry_signal=False,
            exit_signal=bool(long_exit or short_exit),
            price=round(price_now, 4),
            direction="long",
            confidence=0.0,
            indicators={
                "atr_14": round(atr_now, 6),
                "bb_width": round(float(bb_width.iloc[-1]), 4),
                "ema_200": round(ema_now, 4),
                "rsi": round(rsi_now, 1),
                "squeezed": is_squeezed,
            },
        )

    def parameter_space(self) -> dict:
        return {
            "atr_percentile": (10, 40, 5),
            "bb_width_multiplier": (0.3, 0.8, 0.1),
            "ema_period": (100, 300, 50),
            "rsi_period": (7, 21, 7),
        }


STRATEGY_CLASS = VolatilityContractionBreakoutStrategy

STRATEGIES = [
    ("S-VCB-BTC", VolatilityContractionBreakoutStrategy, {"_asset": "BTC"}),
    ("S-VCB-ETH", VolatilityContractionBreakoutStrategy, {"_asset": "ETH"}),
]