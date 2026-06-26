"""Registration-time lookahead / data-leak probe.

An AI-generated strategy that uses a future bar in its vectorized
``generate_signals`` (e.g. ``.shift(-1)``) gets 1-bar lookahead and produces
impossible metrics (Sharpe pegged at the +/-10 clamp, profit factor 12-15, win
rate ~79%, thousands-of-percent returns). The promotion gates struggle to catch
this because a uniform leak makes BOTH the IS and OOS slices amazing (so the
IS/OOS-gap overfit detector sees gap ~0) and keeps profit factor high (so the
win-rate trap, which needs PF < 1.2, never fires).

This module catches the bug at the source via a **truncation-invariance probe**:
a genuinely causal signal at bar ``t`` must be identical whether or not bars
*after* ``t`` exist in the frame. If withholding future bars changes the signal
at an interior bar, the strategy reads the future. This is high-precision
(near-zero false positives) -- a correctly written causal strategy is invariant
under right-truncation by construction.

The probe NEVER raises: any error (the strategy throwing, an un-normalizable
payload) returns ``None`` so a probe failure can't block legitimate
registration. The bug is the leak, not the probe.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Interior bars (counted from the end) at which to compare full-frame vs
# truncated-frame signals. All are well away from the warm-up region at the
# start of the frame so rolling-window NaNs don't cause spurious diffs.
_PROBE_OFFSETS = (60, 40, 20, 5)
_SYNTHETIC_ROWS = 300


def _build_synthetic_ohlcv(rows: int = _SYNTHETIC_ROWS) -> pd.DataFrame:
    """Deterministic synthetic OHLCV frame with the optional order-flow columns.

    Seeded RNG (no global/Date.now randomness) so the probe is reproducible and
    a flaky strategy can't pass by luck on one run and fail on another.
    """
    rng = np.random.default_rng(7)
    n = int(rows)

    index = pd.date_range("2023-01-01", periods=n, freq="1h")

    # Geometric random walk for close (positive, realistically noisy).
    log_returns = rng.normal(loc=0.0, scale=0.01, size=n)
    close = 30_000.0 * np.exp(np.cumsum(log_returns))

    # Derive a sane OHLC envelope around the close path.
    prev_close = np.empty(n, dtype=float)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    open_ = prev_close
    span = np.abs(rng.normal(loc=0.0, scale=0.004, size=n)) * close
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    low = np.maximum(low, 1.0)  # keep strictly positive
    volume = rng.uniform(low=100.0, high=1_000.0, size=n)

    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )

    # Optional enrichment columns the order-flow strategies consume. Plausible
    # non-zero values so a strategy that reads them doesn't divide-by-zero or
    # early-out to an all-False signal (which would hide a leak in those cols).
    df["funding_rate"] = rng.normal(loc=0.0001, scale=0.0002, size=n)
    df["open_interest"] = rng.uniform(low=1e6, high=5e6, size=n)
    df["taker_buy_sell_ratio"] = rng.normal(loc=1.0, scale=0.15, size=n).clip(0.1, 5.0)
    df["ls_ratio"] = rng.normal(loc=1.0, scale=0.15, size=n).clip(0.1, 5.0)
    df["long_liq_usd"] = rng.uniform(low=0.0, high=5e5, size=n)
    df["short_liq_usd"] = rng.uniform(low=0.0, high=5e5, size=n)
    df["liq_imbalance"] = rng.uniform(low=-1.0, high=1.0, size=n)

    return df


def _normalize_to_bool_arrays(payload: object, index: pd.Index) -> dict[str, np.ndarray] | None:
    """Normalize a generate_signals payload to {side: bool ndarray} aligned to index.

    Mirrors how ``backtest._normalize_directional_signal_payload`` /
    ``_resolve_strategy_vectorized_signals`` interpret the payload -- the 2-tuple
    ``(entry, exit)`` (treated as long), the 4-tuple
    ``(long_entries, long_exits, short_entries, short_exits)``, and a
    ``DirectionalSignals`` object. Returns ``None`` if the payload can't be
    interpreted (probe degrades gracefully).
    """
    from axiom.strategies.base import DirectionalSignals

    def _coerce(series: object) -> np.ndarray:
        s = pd.Series(series)
        # Align to the frame index when the series carries a comparable index,
        # then fill gaps with False and cast to bool (matches _coerce_bool_series
        # semantics closely enough for a flip-comparison).
        try:
            if isinstance(s.index, pd.DatetimeIndex) or s.index.equals(index):
                s = s.reindex(index)
        except Exception:
            pass
        return s.fillna(False).to_numpy(dtype=bool)

    if isinstance(payload, DirectionalSignals):
        return {
            "long_entries": _coerce(payload.long_entries),
            "long_exits": _coerce(payload.long_exits),
            "short_entries": _coerce(payload.short_entries),
            "short_exits": _coerce(payload.short_exits),
        }
    if isinstance(payload, (tuple, list)) and len(payload) == 4:
        return {
            "long_entries": _coerce(payload[0]),
            "long_exits": _coerce(payload[1]),
            "short_entries": _coerce(payload[2]),
            "short_exits": _coerce(payload[3]),
        }
    if isinstance(payload, (tuple, list)) and len(payload) == 2:
        # 2-tuple is (entries, exits) treated as the long side (mirrors the
        # long_only default in _normalize_directional_signal_payload).
        return {
            "long_entries": _coerce(payload[0]),
            "long_exits": _coerce(payload[1]),
        }
    return None


def detect_lookahead(strategy_obj) -> str | None:
    """Return a rejection reason if ``strategy_obj`` reads future bars, else None.

    Runs a truncation-invariance probe: computes vectorized signals on a full
    synthetic frame, then recomputes on right-truncated frames ``df.iloc[:t+1]``
    for several interior bars ``t`` and checks the signal AT bar ``t`` is
    unchanged. Any flip means the bar-``t`` signal depended on bars after ``t``
    (a lookahead leak, e.g. ``.shift(-1)``).

    Graceful: returns ``None`` (never raises) if the strategy lacks
    ``generate_signals``, throws, or produces an un-normalizable payload -- a
    probe error must not block registration.
    """
    if strategy_obj is None or not hasattr(strategy_obj, "generate_signals"):
        # Nothing to probe vectorized; the per-bar path is checked elsewhere.
        return None

    try:
        df = _build_synthetic_ohlcv()
        index = df.index

        full_payload = strategy_obj.generate_signals(df)
        if full_payload is None:
            return None
        full = _normalize_to_bool_arrays(full_payload, index)
        if full is None:
            return None

        n = len(df)
        for offset in _PROBE_OFFSETS:
            t = n - offset
            if t <= 1 or t >= n:
                continue
            truncated = df.iloc[: t + 1]
            trunc_payload = strategy_obj.generate_signals(truncated)
            if trunc_payload is None:
                continue
            trunc = _normalize_to_bool_arrays(trunc_payload, truncated.index)
            if trunc is None:
                continue

            for side, full_arr in full.items():
                trunc_arr = trunc.get(side)
                if trunc_arr is None:
                    continue
                if t >= len(full_arr) or t >= len(trunc_arr):
                    continue
                if bool(full_arr[t]) != bool(trunc_arr[t]):
                    return (
                        f"Lookahead detected: vectorized signal at bar t=-{offset} "
                        f"changes when future bars are withheld ({side}) -- strategy "
                        f"reads future data (e.g. a .shift(-1)); rejected"
                    )
        return None
    except Exception as exc:  # never block registration on a probe error
        log.warning("Lookahead probe error (treated as inconclusive): %s", exc)
        return None


__all__ = ["detect_lookahead"]
