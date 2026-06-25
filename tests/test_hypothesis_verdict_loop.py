import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from axiom.db import get_db
from axiom.hypotheses import create_hypothesis


def _hyp():
    return create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )


def _add_children(hypothesis_id: str, n: int):
    with get_db() as conn:
        for i in range(n):
            conn.execute(
                """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
                   stage, status, hypothesis_id, owner, params, metrics, verdict, created_at, updated_at)
                   VALUES (?, ?, 'c', 'rsi', 'BTC', '1h', 'quick_screen', 'active', ?, 'brain',
                           '{}', '{}', '{}', datetime('now'), datetime('now'))""",
                (f"S_CHILD_{hypothesis_id[-4:]}_{i}", f"S9{i:04d}", hypothesis_id),
            )


def test_loop_triggers_on_n_strategy_threshold(AXIOM_db):
    from axiom.hypothesis_verdict import run_verdict_loop
    hyp = _hyp()
    _add_children(hyp["id"], 3)
    fake = json.dumps({"verdict": "researching", "rationale": "keep trying"})
    with patch("axiom.hypothesis_verdict._call_llm", return_value=fake):
        result = run_verdict_loop()
    assert hyp["id"] in result["processed_ids"]


def test_loop_skips_hypothesis_under_threshold(AXIOM_db):
    from axiom.hypothesis_verdict import run_verdict_loop
    hyp = _hyp()
    _add_children(hyp["id"], 1)  # below _N_TRIGGER=2
    with patch("axiom.hypothesis_verdict._call_llm") as m:
        result = run_verdict_loop()
    assert hyp["id"] not in result["processed_ids"]
    m.assert_not_called()


def test_loop_triggers_on_staleness(AXIOM_db):
    from axiom.hypothesis_verdict import run_verdict_loop
    hyp = _hyp()
    _add_children(hyp["id"], 1)
    # Backdate last memo to 8 days ago
    stamp = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET verdict_memo_at = ?, verdict_memo_by = 'agent:strategy-developer' WHERE id = ?",
            (stamp, hyp["id"]),
        )
    fake = json.dumps({"verdict": "researching", "rationale": "still trying"})
    with patch("axiom.hypothesis_verdict._call_llm", return_value=fake):
        result = run_verdict_loop()
    assert hyp["id"] in result["processed_ids"]


def test_loop_skips_disproven(AXIOM_db):
    from axiom.hypothesis_verdict import run_verdict_loop
    from axiom.hypotheses import update_hypothesis_status
    hyp = _hyp()
    _add_children(hyp["id"], 5)
    update_hypothesis_status(hyp["id"], new_status="disproven",
                             memo={"verdict": "disproven", "rationale": "x"}, by="test")
    with patch("axiom.hypothesis_verdict._call_llm") as m:
        result = run_verdict_loop()
    assert hyp["id"] not in result["processed_ids"]
    m.assert_not_called()


def test_loop_sweeps_stranded_proven(AXIOM_db):
    """A hypothesis stuck at status='proven' but manager_state='active'
    (graduation previously failed) is completed by the sweep so its winners
    can become canonical and deploy."""
    from axiom.hypothesis_verdict import run_verdict_loop
    hyp = _hyp()
    _add_children(hyp["id"], 3)
    # Simulate the stranded state: status set proven, but never graduated.
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET status = 'proven', manager_state = 'active' WHERE id = ?",
            (hyp["id"],),
        )
    with patch("axiom.hypothesis_verdict._call_llm") as m:
        result = run_verdict_loop()
    assert hyp["id"] in result["swept_ids"]
    m.assert_not_called()  # proven hypotheses are not re-evaluated by the LLM
    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state, status FROM hypotheses WHERE id = ?", (hyp["id"],)
        ).fetchone()
    assert row["manager_state"] == "graduated"
    assert row["status"] == "proven"
