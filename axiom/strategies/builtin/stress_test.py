"""Stress Test strategy - deterministic trade cadence for system validation."""

from __future__ import annotations

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "stress_test"


def _coerce_int(value: object, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, minimum)


def _resolve_stress_schedule(params: dict | None) -> tuple[int, int, int]:
    payload = params or {}

    hold_bars = _coerce_int(payload.get("hold_bars"), 1, 1)
    phase_offset = _coerce_int(payload.get("phase_offset"), 0, 0)

    if "flat_bars" in payload:
        flat_bars = _coerce_int(payload.get("flat_bars"), 1, 1)
    else:
        try:
            frequency = float(payload.get("frequency", 0.9))
        except (TypeError, ValueError):
            frequency = 0.9
        if frequency >= 0.85:
            flat_bars = 1
        elif frequency >= 0.6:
            flat_bars = 2
        else:
            flat_bars = 3

    return hold_bars, flat_bars, phase_offset


def _infer_bar_seconds(index: pd.Index) -> int:
    timestamps = pd.DatetimeIndex(pd.to_datetime(index, utc=True, errors="coerce"))
    valid = timestamps[~timestamps.isna()]
    if len(valid) < 2:
        return 60

    deltas = valid.to_series().diff().dropna().dt.total_seconds()
    positive = deltas[deltas > 0]
    if positive.empty:
        return 60

    return max(int(round(float(positive.median()))), 1)


def _schedule_phases(index: pd.Index, *, hold_bars: int, flat_bars: int, phase_offset: int) -> tuple[pd.Series, int, int]:
    cycle_bars = max(int(hold_bars) + int(flat_bars), 2)
    bar_seconds = _infer_bar_seconds(index)
    timestamps = pd.DatetimeIndex(pd.to_datetime(index, utc=True, errors="coerce"))

    phases: list[int] = []
    fallback_bar = 0
    for stamp in timestamps:
        if pd.isna(stamp):
            phases.append((fallback_bar + phase_offset) % cycle_bars)
            fallback_bar += 1
            continue
        epoch_bar = int(stamp.timestamp() // bar_seconds)
        phases.append((epoch_bar + phase_offset) % cycle_bars)

    return pd.Series(phases, index=index, dtype=int), cycle_bars, bar_seconds


class StressTestStrategy(BaseStrategy):
    """Deterministic validation strategy that opens and closes on a fixed schedule."""

    @property
    def name(self) -> str:
        return f"System Validation ({self.asset})"

    @property
    def asset(self) -> str:
        return str(self.params.get("_asset") or "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "leverage": 1.0,
            "hold_bars": 1,
            "flat_bars": 1,
            "phase_offset": 0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "TREND_DOWN", "RANGE_BOUND", "HIGH_VOL"}

    def describe(self) -> str:
        hold_bars, flat_bars, _phase_offset = _resolve_stress_schedule(self.params)
        return (
            "Deterministic system-validation strategy. "
            f"It holds for {hold_bars} bar(s), idles for {flat_bars} bar(s), "
            "and repeats so backtest, paper, and live runs can be compared directly."
        )

    def _phase_details(self, index: pd.Index) -> tuple[pd.Series, int, int, int, int]:
        hold_bars, flat_bars, phase_offset = _resolve_stress_schedule(self.params)
        phases, cycle_bars, bar_seconds = _schedule_phases(
            index,
            hold_bars=hold_bars,
            flat_bars=flat_bars,
            phase_offset=phase_offset,
        )
        return phases, cycle_bars, bar_seconds, hold_bars, flat_bars

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if df.empty or "close" not in df.columns:
            return Signal(price=0.0, direction="long", confidence=0.0)
        close = pd.to_numeric(df["close"], errors="coerce")
        curr_close = float(close.iloc[-1]) if not close.empty else 0.0

        phases, cycle_bars, bar_seconds, hold_bars, flat_bars = self._phase_details(df.index)
        phase = int(phases.iloc[-1]) if not phases.empty else 0
        entry = phase == 0
        exit_ = phase == hold_bars

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_),
            price=round(curr_close, 4),
            direction="long",
            confidence=1.0,
            indicators={
                "phase": phase,
                "cycle_bars": cycle_bars,
                "hold_bars": hold_bars,
                "flat_bars": flat_bars,
                "bar_interval_seconds": bar_seconds,
            },
        )

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        if df.empty:
            empty = pd.Series(dtype=bool, index=df.index)
            return empty, empty

        phases, _cycle_bars, _bar_seconds, hold_bars, _flat_bars = self._phase_details(df.index)
        entry_signals = phases.eq(0)
        exit_signals = phases.eq(hold_bars)
        return entry_signals.fillna(False), exit_signals.fillna(False)

    def parameter_space(self) -> dict:
        return {
            "hold_bars": (1, 3, 1),
            "flat_bars": (1, 4, 1),
            "phase_offset": (0, 5, 1),
        }


STRATEGY_CLASS = StressTestStrategy
STRATEGIES = [
    ("SYSTEM_VALIDATION_BTC", StressTestStrategy, {"_asset": "BTC", "hold_bars": 1, "flat_bars": 1}),
    ("SYSTEM_VALIDATION_SOL", StressTestStrategy, {"_asset": "SOL", "hold_bars": 1, "flat_bars": 1}),
    ("S00014", StressTestStrategy, {"_asset": "BTC/USDT", "hold_bars": 1, "flat_bars": 1}),
    ("STRESS01", StressTestStrategy, {"_asset": "SOL", "hold_bars": 1, "flat_bars": 1}),
    ("STRESS02", StressTestStrategy, {"_asset": "BTC", "hold_bars": 1, "flat_bars": 1}),
]
