"""Funding-data gate + honest archival (2026-06-14).

Root cause of "winners getting archived": a strategy that passed every robustness
test (S00955, BNB, robustness 100) was held out of paper by the funding-data
completeness gate because BNB funding was never collected, then archived by
gauntlet_sweep with a hardcoded *wrong* reason ("did not pass the robustness
gate"), which made the operator misdiagnose it as a walk-forward failure.

1. The funding gate is stage-aware: it BLOCKS ->live_graduated but ALLOWS ->paper
   (testnet measures real funding; "strict live / achievable paper").
2. The funding reconcile covers every asset strategies trade, not just the scan
   set — so the BNB/XRP/AVAX/... data gap self-heals.
3. demote_failed_gate_strategies records the REAL failure reason, not a blanket
   "robustness gate" label.
"""

import json
from datetime import datetime, timezone

from axiom.db import get_db


def _insert_strategy(conn, sid, *, symbol="BNB", funding_complete=False, stage="gauntlet"):
    metrics = {
        "funding_applied": True,
        "funding_complete": funding_complete,
        "robustness_score": 100.0,
        "sharpe_ratio": 2.5,
    }
    conn.execute(
        "INSERT INTO strategies (id, name, type, stage, symbol, timeframe, metrics, created_at) "
        "VALUES (?, ?, ?, ?, ?, '1h', ?, ?)",
        (sid, sid, "rsi_momentum", stage, symbol, json.dumps(metrics),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# --- 1. Stage-aware funding gate -----------------------------------------

def test_funding_incomplete_allowed_into_paper(AXIOM_db):
    from axiom.policy import evaluate_promotion

    with get_db() as conn:
        _insert_strategy(conn, "fund-paper", funding_complete=False)
    ok, msg = evaluate_promotion("fund-paper", "gauntlet", "paper")
    # Funding-incomplete must NOT be the reason it's held out of paper. (It may
    # still fail other gates, but never the funding one at the paper stage.)
    assert "funding" not in msg.lower(), msg


def test_funding_incomplete_blocks_live(AXIOM_db):
    from axiom.policy import evaluate_promotion

    with get_db() as conn:
        _insert_strategy(conn, "fund-live", funding_complete=False, stage="paper")
    ok, msg = evaluate_promotion("fund-live", "paper", "live_graduated")
    assert not ok
    assert "funding data incomplete" in msg.lower()
    assert "live" in msg.lower()


def test_funding_complete_not_blocked_for_live(AXIOM_db):
    from axiom.policy import evaluate_promotion

    with get_db() as conn:
        _insert_strategy(conn, "fund-ok", funding_complete=True, stage="paper")
    _ok, msg = evaluate_promotion("fund-ok", "paper", "live_graduated")
    # Whatever else happens, it is NOT held for funding when funding is complete.
    assert "funding" not in msg.lower(), msg


# --- 2. Reconcile covers strategy assets ---------------------------------

def test_funding_reconcile_covers_strategy_assets(AXIOM_db):
    from axiom.market_data_collector import _funding_reconcile_assets

    with get_db() as conn:
        _insert_strategy(conn, "u-bnb", symbol="BNB")
        _insert_strategy(conn, "u-xrp", symbol="XRP/USDT")
        _insert_strategy(conn, "u-btc", symbol="BTCUSDT")
        _insert_strategy(conn, "u-junk", symbol="GENERIC")
    assets = _funding_reconcile_assets()
    assert "BNB" in assets and "XRP" in assets  # off-scan strategy assets covered
    assert "BTC" in assets  # BTCUSDT normalized to BTC
    assert "GENERIC" not in assets and "USDT" not in assets  # junk dropped


# --- 3. Honest archival reason -------------------------------------------

def test_sweep_archives_with_real_reason(AXIOM_db):
    from axiom.gauntlet.engine import _failed_gate_reason, init_gauntlet_schema

    with get_db() as conn:
        init_gauntlet_schema(conn)
        _insert_strategy(conn, "S-honest", funding_complete=False)
        conn.execute(
            "INSERT INTO gauntlet_workflows (id, strategy_id, status, definition_version, created_at, updated_at) "
            "VALUES ('wf-h', 'S-honest', 'failed_gate', 2, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT INTO gauntlet_steps (workflow_id, step_key, status, order_index, updated_at, error_json) "
            "VALUES ('wf-h', 'paper_promotion_gate', 'failed_gate', 11, ?, ?)",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps({"message": "Gate failure: Funding data incomplete — backfill funding history"})),
        )
        conn.commit()

    reason = _failed_gate_reason("S-honest")
    assert reason is not None
    assert "funding data incomplete" in reason.lower()
    assert "robustness" not in reason.lower()  # the old blanket lie is gone
    assert not reason.startswith("Gate failure:")  # redundant prefix stripped
