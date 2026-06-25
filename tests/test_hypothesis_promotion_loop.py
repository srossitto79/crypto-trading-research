import json
from unittest.mock import patch

from axiom.db import get_db
from axiom.hypotheses import create_hypothesis, update_hypothesis_status


def _hyp(status="researching"):
    h = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )
    if status != "proposed":
        update_hypothesis_status(h["id"], new_status=status,
                                 memo={"verdict": status, "rationale": "seed"}, by="test")
    return h


def test_promotion_loop_skips_disproven(AXIOM_db):
    from axiom.hypothesis_promotion import run_promotion_loop
    h = _hyp(status="disproven")
    with patch("axiom.brain.assign_task") as m:
        result = run_promotion_loop(top_k=3)
    assert h["id"] not in result["dispatched_ids"]
    m.assert_not_called()


def test_promotion_loop_dispatches_top_k_by_promise(AXIOM_db):
    from axiom.hypothesis_promotion import run_promotion_loop
    hot = _hyp()
    cold = _hyp()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
               stage, status, hypothesis_id, owner, params, metrics, verdict, created_at, updated_at)
               VALUES ('S_HOT_1', 'S91000', 'h', 'rsi', 'BTC', '1h', 'quick_screen',
                       'active', ?, 'brain', '{}', '{}', ?, datetime('now'), datetime('now'))""",
            (hot["id"], json.dumps({"lifecycle": "paper_eligible"})),
        )
    with patch("axiom.brain.assign_task", return_value=999) as m:
        result = run_promotion_loop(top_k=1)
    assert result["dispatched_ids"] == [hot["id"]]
    m.assert_called_once()
    kwargs = m.call_args.kwargs
    assert kwargs["task_type"] == "develop_candidate"
    assert kwargs["input_data"]["origin_mode"] == "hypothesis_promotion_loop"
    assert kwargs["input_data"]["action_kind"] == "develop_candidate"
    assert kwargs["input_data"]["crucible_id"] == hot["id"]
    assert kwargs["input_data"]["hypothesis_id"] == hot["id"]


def test_promotion_loop_skips_proposed_hypotheses(AXIOM_db):
    from axiom.hypothesis_promotion import run_promotion_loop

    h = _hyp(status="proposed")

    with patch("axiom.brain.assign_task") as m:
        result = run_promotion_loop(top_k=3)

    assert h["id"] not in result["dispatched_ids"]
    m.assert_not_called()


def test_promotion_loop_respects_cooldown(AXIOM_db):
    from axiom.hypothesis_promotion import run_promotion_loop
    h = _hyp()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute("UPDATE hypotheses SET last_dispatched_at = ? WHERE id = ?", (now_iso, h["id"]))
    with patch("axiom.brain.assign_task") as m:
        result = run_promotion_loop(top_k=1)
    assert h["id"] not in result["dispatched_ids"]


def test_promotion_loop_respects_global_cap(AXIOM_db):
    """When MAX_IN_FLIGHT is hit, dispatches nothing new."""
    from axiom.hypothesis_promotion import run_promotion_loop
    _hyp(); _hyp(); _hyp()
    with patch("axiom.hypothesis_promotion._current_in_flight_task_count", return_value=99):
        with patch("axiom.brain.assign_task") as m:
            result = run_promotion_loop(top_k=3, max_in_flight=5)
    assert result["dispatched_ids"] == []
    m.assert_not_called()
