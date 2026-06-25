"""Phase 3: round-robin depth enforcement.

Once a hypothesis has been picked, the scheduler must NOT pick it again until
it has accumulated `min_strategies_per_pick` children since the last pick.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from axiom.db import get_db, kv_set
from axiom.hypotheses import create_hypothesis


def _hyp(idx: int) -> dict:
    h = create_hypothesis(
        title=f"H{idx}",
        market_thesis=f"thesis {idx}",
        mechanism="m",
        why_now="n",
        lane="benchmarking",
        source_type="agent_original",
        origin_agent_id="a",
        origin_role="strategy-developer",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    # New hypotheses are 'proposed' (un-refined) and _score_rows skips them;
    # the crucible_planner promotes proposed->researching after refinement.
    # These tests exercise the round-robin DEPTH gate, so seed the refined
    # 'researching' state — otherwise the proposed-skip would mask the gate.
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET status = 'researching' WHERE id = ?", (h["id"],)
        )
        conn.commit()
    return h


def _set_min(min_per_pick: int, cap: int = 10) -> None:
    kv_set(
        "axiom:settings",
        {
            "research_settings": {
                "hypothesis_discipline": {
                    "min_strategies_per_pick": min_per_pick,
                    "active_pool_cap": cap,
                }
            }
        },
    )


def _seed_strategy(hypothesis_id: str, sid: str, created_at: str) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
               stage, status, hypothesis_id, owner, params, metrics, verdict,
               created_at, updated_at)
               VALUES (?, ?, 'n', 'rsi', 'BTC', '1h', 'quick_screen',
                       'active', ?, 'brain', '{}', '{}', '{}', ?, ?)""",
            (sid, sid, hypothesis_id, created_at, created_at),
        )


def _set_last_picked(hypothesis_id: str, when: datetime) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET last_dispatched_at = ? WHERE id = ?",
            (when.isoformat(), hypothesis_id),
        )


def _bypass_cooldown(hypothesis_id: str) -> None:
    """Cooldown is 15 min — set last_dispatched_at to 30 min ago to bypass."""
    _set_last_picked(
        hypothesis_id, datetime.now(timezone.utc) - timedelta(minutes=30)
    )


def test_first_pick_bypasses_depth_gate(AXIOM_db):
    """A hypothesis that has never been picked is always eligible (no
    last_dispatched_at means no in-progress depth requirement)."""
    from axiom.hypothesis_promotion import _score_rows

    _set_min(3)
    _hyp(0)
    rows = _score_rows()
    assert len(rows) == 1


def test_pick_blocked_until_min_strategies_reached(AXIOM_db):
    """A hypothesis picked once but with only 2 children since (min=3) is
    excluded from the next pick."""
    from axiom.hypothesis_promotion import _score_rows

    _set_min(3)
    h = _hyp(0)
    picked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _set_last_picked(h["id"], picked_at)
    after = (picked_at + timedelta(seconds=1)).isoformat()
    _seed_strategy(h["id"], "S1", after)
    _seed_strategy(h["id"], "S2", after)

    rows = _score_rows()
    assert rows == []


def test_pick_allowed_after_min_strategies_reached(AXIOM_db):
    """Same as above but with 3 children since pick — eligible."""
    from axiom.hypothesis_promotion import _score_rows

    _set_min(3)
    h = _hyp(0)
    picked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _set_last_picked(h["id"], picked_at)
    after = (picked_at + timedelta(seconds=1)).isoformat()
    _seed_strategy(h["id"], "S1", after)
    _seed_strategy(h["id"], "S2", after)
    _seed_strategy(h["id"], "S3", after)

    rows = _score_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == h["id"]


def test_strategies_before_pick_do_not_count(AXIOM_db):
    """Children created BEFORE last_dispatched_at don't count toward the
    post-pick depth — only new ones do."""
    from axiom.hypothesis_promotion import _score_rows

    _set_min(3)
    h = _hyp(0)
    # Three children already exist before pick — but they're "old" work
    pre = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _seed_strategy(h["id"], "S_OLD_1", pre)
    _seed_strategy(h["id"], "S_OLD_2", pre)
    _seed_strategy(h["id"], "S_OLD_3", pre)
    # Pick now
    picked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _set_last_picked(h["id"], picked_at)
    # No new children since pick → depth gate blocks
    rows = _score_rows()
    assert rows == []


def test_round_robin_distributes_picks(AXIOM_db):
    """3 hypotheses, min=2. Tick A picks H_a, then 2 children land. Tick B
    must pick H_b (or H_c), not H_a again."""
    from axiom.hypothesis_promotion import _score_rows

    _set_min(2)
    h_a = _hyp(0)
    h_b = _hyp(1)
    h_c = _hyp(2)
    picked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _set_last_picked(h_a["id"], picked_at)
    after = (picked_at + timedelta(seconds=1)).isoformat()
    _seed_strategy(h_a["id"], "S1", after)
    _seed_strategy(h_a["id"], "S2", after)
    # H_a now has 2 children since pick — meets threshold, eligible
    # H_b, H_c never picked — eligible
    rows = _score_rows()
    ids = {r["id"] for r in rows}
    assert ids == {h_a["id"], h_b["id"], h_c["id"]}


def test_run_promotion_loop_logs_no_eligible(AXIOM_db, caplog):
    """When all hypotheses are depth-blocked, run_promotion_loop returns
    empty result with no_eligible flag and logs the structured message."""
    import logging

    from axiom.hypothesis_promotion import run_promotion_loop

    _set_min(5)
    h = _hyp(0)
    picked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _set_last_picked(h["id"], picked_at)
    # Only 1 child since pick → blocked (need 5)
    after = (picked_at + timedelta(seconds=1)).isoformat()
    _seed_strategy(h["id"], "S1", after)

    with caplog.at_level(logging.INFO, logger="axiom.hypothesis_promotion"):
        with patch("axiom.brain.assign_task") as mock:
            result = run_promotion_loop(top_k=3)
    assert result["dispatched_ids"] == []
    assert result["picked"] == 0
    assert result["skipped"].get("no_eligible") == 1
    mock.assert_not_called()
    assert any("no_eligible_hypothesis" in rec.message for rec in caplog.records)


def test_disproven_strategies_still_count_for_depth(AXIOM_db):
    """Children with negative outcomes still count toward depth — depth is
    about effort spent, not success."""
    from axiom.hypothesis_promotion import _score_rows

    _set_min(3)
    h = _hyp(0)
    picked_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _set_last_picked(h["id"], picked_at)
    after = (picked_at + timedelta(seconds=1)).isoformat()
    # 3 children, all with non-positive verdicts
    _seed_strategy(h["id"], "S1", after)
    _seed_strategy(h["id"], "S2", after)
    _seed_strategy(h["id"], "S3", after)

    rows = _score_rows()
    assert len(rows) == 1
