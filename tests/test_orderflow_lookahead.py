"""Regression tests for the sub-1h order-flow look-ahead fix.

The 1h order-flow aggregate streams (taker_buy_sell_ratio / ls_ratio /
liquidations) are stamped at bucket START but summarize the forward
[t, t+1h) window, so a row is only knowable at bucket CLOSE. A naive backward
``merge_asof`` onto finer bars exposed the in-progress hour to 5m/15m bars =
look-ahead (fake ~Sharpe-10 order-flow "edges"). ``_merge_asof_parquet`` now
re-stamps these streams to bucket close via ``shift_to_bucket_close=True``.
"""
from __future__ import annotations

import inspect

import pandas as pd

from axiom import data_manager as dm
from axiom.data_manager import _merge_asof_parquet


def test_bucket_close_shift_is_causal(tmp_path):
    # 1h buckets stamped at START; value encodes the hour (10 + hour index).
    src = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01 00:00", periods=8, freq="1h", tz="UTC"),
            "taker_buy_sell_ratio": [10.0 + i for i in range(8)],
        }
    )
    p = tmp_path / "taker_volume_1h.parquet"
    src.to_parquet(p)
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01 02:00", periods=4, freq="15min", tz="UTC"),
            "close": 1.0,
        }
    )

    leak = _merge_asof_parquet(
        bars.copy(), p, cols=["taker_buy_sell_ratio"], fill={"taker_buy_sell_ratio": 0.0},
        shift_to_bucket_close=False,
    )
    fix = _merge_asof_parquet(
        bars.copy(), p, cols=["taker_buy_sell_ratio"], fill={"taker_buy_sell_ratio": 0.0},
        shift_to_bucket_close=True,
    )

    # Unshifted: a 15m bar at 02:00 reads the IN-PROGRESS 02:00 bucket (12.0) => leak.
    assert leak["taker_buy_sell_ratio"].iloc[0] == 12.0
    # Shifted: every 02:xx bar reads only the last CLOSED hour, the 01:00 bucket (11.0).
    assert (fix["taker_buy_sell_ratio"] == 11.0).all()


def test_shift_handles_irregular_and_short_sources(tmp_path):
    # < 3 rows: cannot infer a bucket width -> no shift, no crash, df returned.
    src = pd.DataFrame(
        {"timestamp": pd.date_range("2026-01-01 00:00", periods=2, freq="1h", tz="UTC"), "ls_ratio": [1.0, 2.0]}
    )
    p = tmp_path / "long_short_ratio_1h.parquet"
    src.to_parquet(p)
    bars = pd.DataFrame({"timestamp": pd.date_range("2026-01-01 05:00", periods=2, freq="15min", tz="UTC"), "close": 1.0})
    out = _merge_asof_parquet(bars.copy(), p, cols=["ls_ratio"], fill={"ls_ratio": 0.0}, shift_to_bucket_close=True)
    assert "ls_ratio" in out.columns  # graceful, no exception


def test_orderflow_enrichers_request_bucket_close_shift():
    # The three forward-window AGGREGATE enrichers MUST request the shift; the
    # forward-announced (funding) and point-in-time (open_interest) enrichers
    # MUST NOT, or the fix would corrupt their correct backward-join semantics.
    # data_manager is a lazy proxy; getattr forwards to the real instance, giving
    # bound methods that inspect.getsource can resolve to their source.
    inst = dm.data_manager
    for name in ("_enrich_long_short_ratio", "_enrich_taker_volume", "_enrich_liquidations"):
        src = inspect.getsource(getattr(inst, name))
        assert "shift_to_bucket_close=True" in src, f"{name} must shift 1h aggregates to bucket close"
    for name in ("_enrich_funding", "_enrich_oi"):
        src = inspect.getsource(getattr(inst, name))
        assert "shift_to_bucket_close" not in src, f"{name} must NOT shift (forward-announced / point-in-time)"
