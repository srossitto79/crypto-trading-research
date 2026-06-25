"""Quality-aware gauntlet scheduling.

Under a backlog larger than a tick's visit budget (the common case in a
registration flood — the quick_screen stage is not WIP-capped), the order in
which active workflows are visited decides which strategies advance toward paper.
``list_active_workflow_ids`` now advances the most-promising strategies first
(headline Sharpe), and ``backfill_missing_quick_screen_workflows`` creates
workflows for the most-promising stranded strategies first.

The previous strict oldest-first fairness is preserved as a HARD FLOOR: any
workflow untouched for longer than ``_VISIT_STALENESS_SECONDS`` floats to the
front regardless of score, so a low-ranked workflow is never starved.
"""
from __future__ import annotations

import json

from axiom.db import create_strategy_container, get_db
from axiom.gauntlet.engine import (
    _VISIT_STALENESS_SECONDS,
    _iso_seconds_ago,
    backfill_missing_quick_screen_workflows,
    list_active_workflow_ids,
)
from axiom.gauntlet.settings import build_settings_snapshot
from axiom.gauntlet.store import create_or_get_workflow


def _container(*, sharpe, stage="gauntlet"):
    with get_db() as conn:
        sid, _d, _b = create_strategy_container(
            conn=conn, name=f"S{sharpe}", type_="rsi_momentum", symbol="ETH/USDT",
            timeframe="1h", params={"rsi_period": 14}, stage=stage,
        )
    metrics = json.dumps({"sharpe": sharpe}) if sharpe is not None else "{}"
    with get_db() as conn:
        conn.execute("UPDATE strategies SET metrics=?, stage=? WHERE id=?", (metrics, stage, sid))
        conn.commit()
    return sid


def _workflow(*, sharpe, stage="gauntlet", age_seconds=0):
    sid = _container(sharpe=sharpe, stage=stage)
    wf = create_or_get_workflow(
        strategy_id=sid, created_by="pytest", settings_snapshot=build_settings_snapshot()
    )
    if age_seconds:
        touch = _iso_seconds_ago(age_seconds)
        with get_db() as conn:
            conn.execute(
                "UPDATE gauntlet_workflows SET updated_at=?, created_at=? WHERE id=?",
                (touch, touch, wf["id"]),
            )
            conn.commit()
    return wf["id"]


# --- list_active_workflow_ids: quality tier -------------------------------

def test_fresh_workflows_ordered_by_sharpe_desc(AXIOM_db):
    low = _workflow(sharpe=0.5)
    high = _workflow(sharpe=3.0)
    mid = _workflow(sharpe=1.5)
    order = list_active_workflow_ids()
    assert order.index(high) < order.index(mid) < order.index(low)


def test_null_or_empty_metrics_sort_last_among_fresh(AXIOM_db):
    good = _workflow(sharpe=2.0)
    nometrics = _workflow(sharpe=None)  # '{}' -> json_extract NULL -> last under DESC
    order = list_active_workflow_ids()
    assert order.index(good) < order.index(nometrics)


# --- list_active_workflow_ids: anti-starvation floor ----------------------

def test_stale_low_quality_floats_ahead_of_fresh_high_quality(AXIOM_db):
    fresh_high = _workflow(sharpe=5.0)  # just touched, top Sharpe
    stale_low = _workflow(sharpe=-1.0, age_seconds=_VISIT_STALENESS_SECONDS + 600)
    order = list_active_workflow_ids()
    # The starvation floor wins: the stale one is serviced first DESPITE worse Sharpe.
    assert order.index(stale_low) < order.index(fresh_high)


def test_within_stale_tier_strict_oldest_first(AXIOM_db):
    older = _workflow(sharpe=0.1, age_seconds=_VISIT_STALENESS_SECONDS + 3600)
    newer_stale = _workflow(sharpe=9.9, age_seconds=_VISIT_STALENESS_SECONDS + 600)
    order = list_active_workflow_ids()
    # Both past the floor -> Sharpe ignored, strict oldest-first (the old fairness).
    assert order.index(older) < order.index(newer_stale)


# --- backfill: quality-prioritized self-heal ------------------------------

def test_backfill_creates_higher_sharpe_first_under_limit(AXIOM_db):
    # Two stranded pre-paper strategies, NO workflow yet. With limit=1 only the
    # higher-Sharpe one should get a workflow this pass.
    hi = _container(sharpe=4.0, stage="quick_screen")
    lo = _container(sharpe=0.1, stage="quick_screen")

    created = backfill_missing_quick_screen_workflows(limit=1)
    assert created >= 1

    with get_db() as conn:
        hi_n = conn.execute("SELECT COUNT(*) c FROM gauntlet_workflows WHERE strategy_id=?", (hi,)).fetchone()["c"]
        lo_n = conn.execute("SELECT COUNT(*) c FROM gauntlet_workflows WHERE strategy_id=?", (lo,)).fetchone()["c"]
    assert hi_n >= 1, "higher-Sharpe stranded strategy must be served first"
    assert lo_n == 0, "lower-Sharpe strategy waits for a later pass under the limit"
