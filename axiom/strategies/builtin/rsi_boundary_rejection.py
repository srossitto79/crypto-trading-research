"""RSI Boundary Rejection strategy — trades bounces from RSI oversold/overbought zones."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "rsi_boundary_rejection"


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI helper using Wilder smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


class RSIBoundaryRejectionStrategy(BaseStrategy):
    """RSI boundary rejection — fades moves that overextend into extremes.

    Validates entry with a 3-bar bounce/drop confirmation to reduce false signals.

    Long entry (all must be true):
        1. RSI(14) touched 30 (or below) on the previous bar
        2. RSI now > 35 within 3 bars  → bounce confirmed
        3. close > EMA(200)           → price in uptrend

    Short entry (all must be true):
        1. RSI(14) touched 70 (or above) on the previous bar
        2. RSI now < 65 within 3 bars  → rejection confirmed
        3. close < EMA(200)           → price in downtrend

    Exit: price crosses EMA from the entry direction.
    """

    @property
    def name(self) -> str:
        return f"RSI Boundary Rejection ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "rsi_bounce_confirm": 35,
            "rsi_drop_confirm": 65,
            "confirmation_bars": 3,
            "ema_period": 200,
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND", "TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        return (
            "Fades overextended moves by entering after RSI touches an extreme "
            "boundary (30/70) and confirming a reversal within 3 bars. "
            "Longs require price above EMA200; shorts require price below EMA200."
        )

    def _detect_bounce(self, rsi_vals: pd.Series, oversold: float, bounce_thresh: float, n: int) -> bool:
        """Detect RSI bounce: touched oversold in last n bars and now above bounce_thresh."""
        lookback = rsi_vals.iloc[-n:]
        touched = (lookback <= oversold).any()
        current_above = float(rsi_vals.iloc[-1]) > bounce_thresh
        return bool(touched and current_above)

    def _detect_rejection(self, rsi_vals: pd.Series, overbought: float, drop_thresh: float, n: int) -> bool:
        """Detect RSI rejection: touched overbought in last n bars and now below drop_thresh."""
        lookback = rsi_vals.iloc[-n:]
        touched = (lookback >= overbought).any()
        current_below = float(rsi_vals.iloc[-1]) < drop_thresh
        return bool(touched and current_below)

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params

        min_bars = max(p["confirmation_bars"] + 1, p["rsi_period"], p["ema_period"])
        if len(df) < min_bars:
            return Signal(
                price=round(float(df["close"].iloc[-1]), 4),
                direction="long",
                indicators={"error": "insufficient_bars"},
            )

        close = df["close"]
        rsi_vals = _rsi(close, p["rsi_period"])
        ema = close.ewm(span=p["ema_period"], adjust=False).mean()

        if len(rsi_vals) < p["confirmation_bars"]:
            return Signal(
                price=round(float(close.iloc[-1]), 4),
                direction="long",
                indicators={"error": "insufficient_bars"},
            )

        price_now = float(close.iloc[-1])
        ema_now = float(ema.iloc[-1])
        rsi_now = float(rsi_vals.iloc[-1])

        n = int(p["confirmation_bars"])

        # ── Entry signals ─────────────────────────────────────────────────────
        long_bounce = self._detect_bounce(
            rsi_vals, p["rsi_oversold"], p["rsi_bounce_confirm"], n
        )
        short_rejection = self._detect_rejection(
            rsi_vals, p["rsi_overbought"], p["rsi_drop_confirm"], n
        )

        long_entry = long_bounce and price_now > ema_now
        short_entry = short_rejection and price_now < ema_now

        # ── Exit signals ───────────────────────────────────────────────────────
        long_exit = price_now < ema_now
        short_exit = price_now > ema_now

        if long_entry:
            return Signal(
                entry_signal=True,
                exit_signal=False,
                price=round(price_now, 4),
                direction="long",
                confidence=1.0,
                indicators={
                    "rsi": round(rsi_now, 1),
                    "ema_200": round(ema_now, 4),
                    "long_bounce": True,
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
                    "rsi": round(rsi_now, 1),
                    "ema_200": round(ema_now, 4),
                    "short_rejection": True,
                },
            )

        return Signal(
            entry_signal=False,
            exit_signal=bool(long_exit or short_exit),
            price=round(price_now, 4),
            direction="long",
            confidence=0.0,
            indicators={
                "rsi": round(rsi_now, 1),
                "ema_200": round(ema_now, 4),
                "above_ema": price_now > ema_now,
            },
        )

    def parameter_space(self) -> dict:
        return {
            "rsi_oversold": (20, 35, 5),
            "rsi_overbought": (65, 80, 5),
            "rsi_bounce_confirm": (30, 45, 5),
            "rsi_drop_confirm": (55, 70, 5),
            "confirmation_bars": (2, 5, 1),
            "ema_period": (100, 300, 50),
        }


STRATEGY_CLASS = RSIBoundaryRejectionStrategy

STRATEGIES = [
    ("S-RBR-BTC", RSIBoundaryRejectionStrategy, {"_asset": "BTC"}),
    ("S-RBR-ETH", RSIBoundaryRejectionStrategy, {"_asset": "ETH"}),
]