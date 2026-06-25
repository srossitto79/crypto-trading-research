"""Parabolic SAR strategy."""

from __future__ import annotations

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "parabolic_sar"


def _resolve_psar_params(params: dict | None) -> tuple[float, float]:
    payload = params or {}
    try:
        step = float(payload.get("step", 0.02))
    except (TypeError, ValueError):
        step = 0.02
    try:
        max_step = float(payload.get("max_step", 0.2))
    except (TypeError, ValueError):
        max_step = 0.2

    step = min(max(step, 0.001), 1.0)
    max_step = min(max(max_step, step), 2.0)
    return step, max_step


def parabolic_sar_series(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    """Compute a Parabolic SAR series from OHLC data."""
    if df.empty:
        return pd.Series(dtype=float, index=df.index)

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    if len(df) == 1:
        return pd.Series([float(low.iloc[0])], index=df.index, dtype=float)

    bull = bool(close.iloc[1] >= close.iloc[0])
    psar: list[float] = [float(low.iloc[0] if bull else high.iloc[0])]
    ep = float(high.iloc[0] if bull else low.iloc[0])
    af = float(step)

    for idx in range(1, len(df)):
        prev_psar = psar[-1]
        hi = float(high.iloc[idx])
        lo = float(low.iloc[idx])

        if bull:
            next_psar = prev_psar + af * (ep - prev_psar)
            next_psar = min(next_psar, float(low.iloc[idx - 1]))
            if idx > 1:
                next_psar = min(next_psar, float(low.iloc[idx - 2]))

            if lo < next_psar:
                bull = False
                next_psar = ep
                ep = lo
                af = float(step)
            else:
                if hi > ep:
                    ep = hi
                    af = min(af + step, max_step)
        else:
            next_psar = prev_psar + af * (ep - prev_psar)
            next_psar = max(next_psar, float(high.iloc[idx - 1]))
            if idx > 1:
                next_psar = max(next_psar, float(high.iloc[idx - 2]))

            if hi > next_psar:
                bull = True
                next_psar = ep
                ep = hi
                af = float(step)
            else:
                if lo < ep:
                    ep = lo
                    af = min(af + step, max_step)

        psar.append(float(next_psar))

    return pd.Series(psar, index=df.index, dtype=float)


class ParabolicSARStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return f"Parabolic SAR ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {"step": 0.02, "max_step": 0.2, "leverage": 3.0}

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN"}

    def describe(self) -> str:
        step, max_step = _resolve_psar_params(self.params)
        return f"Trend following using Parabolic SAR (step {step}, max {max_step})."

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        close = pd.to_numeric(df["close"], errors="coerce")
        curr_close = float(close.iloc[-1])
        if len(df) < 3:
            return Signal(
                entry_signal=False,
                exit_signal=False,
                price=round(curr_close, 4),
                direction="long",
                confidence=0.0,
            )

        step, max_step = _resolve_psar_params(self.params)
        sar = parabolic_sar_series(df, step=step, max_step=max_step)

        curr_sar = float(sar.iloc[-1])
        prev_sar = float(sar.iloc[-2])
        prev_close = float(close.iloc[-2])

        entry = prev_close <= prev_sar and curr_close > curr_sar
        exit_ = prev_close >= prev_sar and curr_close < curr_sar

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=1.0 if entry else 0.0,
            indicators={"psar": round(curr_sar, 4)},
        )

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Vectorized signal generation for backtesting using the same PSAR series."""
        step, max_step = _resolve_psar_params(self.params)
        sar = parabolic_sar_series(df, step=step, max_step=max_step)
        close = pd.to_numeric(df["close"], errors="coerce")

        close_prev = close.shift(1)
        sar_prev = sar.shift(1)

        entry_signals = (close_prev <= sar_prev) & (close > sar)
        exit_signals = (close_prev >= sar_prev) & (close < sar)

        return entry_signals.fillna(False), exit_signals.fillna(False)

    def parameter_space(self) -> dict:
        return {"step": (0.01, 0.05, 0.01), "max_step": (0.1, 0.3, 0.1)}


STRATEGY_CLASS = ParabolicSARStrategy

STRATEGIES = [("TOMB-SAR", ParabolicSARStrategy, {"_asset": "BTC"})]
