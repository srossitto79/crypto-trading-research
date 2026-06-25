"""Full-window (IS+OOS) equity curve for the entire-timeframe chart (2026-06-05).

The stored equity_curve is OOS-only (the honest unseen-data window). These cover the
additional full-window curve built for visualization: it spans the entire backtest,
compounding IS trades before OOS, and is compressed + persisted + reloaded intact.
"""
from __future__ import annotations

import pandas as pd

import axiom.api_core as api_core
from axiom.strategies.backtest import (
    _build_equity_curve_from_trades,
    _downsample_curve,
)


def test_full_curve_spans_whole_frame_and_compounds_is_before_oos():
    idx = pd.date_range("2026-01-01", periods=10, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx
    )
    is_trades = [{"exit_time": str(idx[2]), "pnl_pct": 0.1}]   # in-sample (bars 0-6)
    oos_trades = [{"exit_time": str(idx[8]), "pnl_pct": 0.2}]  # out-of-sample (bars 7-9)

    full = _build_equity_curve_from_trades(list(is_trades) + list(oos_trades), df, 1000.0)
    oos_df = df.iloc[7:]
    oos = _build_equity_curve_from_trades(oos_trades, oos_df, 1000.0)

    # Full curve covers every bar; OOS-only covers just the tail.
    assert len(full) == 10
    assert len(oos) == 3
    assert full[0]["timestamp"] == str(idx[0])
    assert oos[0]["timestamp"] == str(idx[7])
    # Full compounds the IS trade (×1.1) AND the OOS trade (×1.2); OOS-only only ×1.2.
    assert full[-1]["equity"] == round(1000.0 * 1.1 * 1.2, 2)
    assert oos[-1]["equity"] == round(1000.0 * 1.2, 2)
    # The OOS boundary (first OOS timestamp) falls strictly inside the full curve.
    assert full[0]["timestamp"] < oos[0]["timestamp"] <= full[-1]["timestamp"]


def test_downsample_collapses_flat_runs_but_keeps_boundaries():
    curve = [{"timestamp": f"t{i}", "equity": 100.0 if i < 25 else 110.0} for i in range(50)]
    ds = _downsample_curve(curve)
    # Endpoints exact; the single step is retained; flat runs collapsed.
    assert ds[0]["timestamp"] == "t0"
    assert ds[-1]["timestamp"] == "t49"
    assert ds[-1]["equity"] == 110.0
    assert len(ds) < len(curve)
    assert any(p["equity"] == 100.0 for p in ds)
    assert any(p["equity"] == 110.0 for p in ds)


def test_downsample_caps_changing_curve_to_max_points():
    changing = [{"timestamp": f"u{i}", "equity": float(i)} for i in range(50)]
    ds = _downsample_curve(changing, max_points=10)
    assert len(ds) <= 10
    assert ds[0]["timestamp"] == "u0"
    assert ds[-1]["timestamp"] == "u49"


def test_full_curves_persist_and_reload(AXIOM_db):
    eq_oos = [
        {"timestamp": "2026-05-20T00:00:00+00:00", "equity": 1000.0},
        {"timestamp": "2026-06-04T00:00:00+00:00", "equity": 1200.0},
    ]
    eq_full = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "equity": 1000.0},
        {"timestamp": "2026-05-20T00:00:00+00:00", "equity": 1500.0},
        {"timestamp": "2026-06-04T00:00:00+00:00", "equity": 1800.0},
    ]
    bm_full = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "equity": 1000.0},
        {"timestamp": "2026-06-04T00:00:00+00:00", "equity": 1100.0},
    ]
    api_core._write_backtest_result_artifacts(
        "eqfull-result", "eqfull-job", [],
        equity_curve=eq_oos,
        benchmark_curve=eq_oos,
        equity_curve_full=eq_full,
        benchmark_curve_full=bm_full,
    )

    arts = api_core._load_result_artifacts("eqfull-result", {}, "backtest")
    assert arts["equity_curve_full"] is not None
    assert len(arts["equity_curve_full"]) == 3
    assert arts["benchmark_curve_full"] is not None
    assert len(arts["benchmark_curve_full"]) == 2
    # OOS curve still present and distinct (shorter window).
    assert arts["equity_curve"] is not None
    assert len(arts["equity_curve"]) == 2
    # The full curve starts earlier than the OOS curve (the whole point).
    assert arts["equity_curve_full"][0]["timestamp"] < arts["equity_curve"][0]["timestamp"]


def test_full_curves_absent_loads_as_none(AXIOM_db):
    # A pre-existing result with only the OOS curve must still load (no full curve).
    api_core._write_backtest_result_artifacts(
        "oosonly-result", "oosonly-job", [],
        equity_curve=[
            {"timestamp": "2026-06-01T00:00:00+00:00", "equity": 1000.0},
            {"timestamp": "2026-06-04T00:00:00+00:00", "equity": 1100.0},
        ],
    )
    arts = api_core._load_result_artifacts("oosonly-result", {}, "backtest")
    assert arts["equity_curve"] is not None
    assert arts["equity_curve_full"] is None
    assert arts["benchmark_curve_full"] is None
