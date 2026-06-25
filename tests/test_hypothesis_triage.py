import json
from unittest.mock import patch

from axiom.db import get_db
from axiom.hypotheses import create_hypothesis


def _hyp_with_child():
    h = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )
    with get_db() as conn:
        conn.execute(
            """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
               stage, status, hypothesis_id, owner, params, metrics, verdict, created_at, updated_at)
               VALUES (?, 'S92000', 'c', 'rsi', 'BTC', '1h', 'quick_screen', 'active',
                       ?, 'brain', '{}', '{}', '{}', datetime('now'), datetime('now'))""",
            (f"S_TRI_{h['id'][-4:]}", h["id"]),
        )
    return h


def test_triage_processes_batch(AXIOM_db):
    from axiom.hypothesis_cleanup import run_triage_loop
    hyps = [_hyp_with_child() for _ in range(3)]
    fake = json.dumps({"verdict": "disproven", "rationale": "garbage"})
    with patch("axiom.hypothesis_verdict._call_llm", return_value=fake):
        result = run_triage_loop(batch_size=10)
    assert result["processed_count"] == 3
    for h in hyps:
        with get_db() as conn:
            row = conn.execute("SELECT status FROM hypotheses WHERE id=?", (h["id"],)).fetchone()
        assert row["status"] == "disproven"


def test_triage_skips_hypotheses_with_existing_memo(AXIOM_db):
    from axiom.hypothesis_cleanup import run_triage_loop
    from axiom.hypotheses import update_hypothesis_status
    h = _hyp_with_child()
    update_hypothesis_status(
        h["id"], new_status="researching",
        memo={"verdict": "researching", "rationale": "prior"}, by="test",
    )
    with patch("axiom.hypothesis_verdict._call_llm") as m:
        result = run_triage_loop(batch_size=10)
    assert h["id"] not in result["processed_ids"]
    m.assert_not_called()


def test_triage_respects_batch_size(AXIOM_db):
    from axiom.hypothesis_cleanup import run_triage_loop
    for _ in range(5):
        _hyp_with_child()
    fake = json.dumps({"verdict": "disproven", "rationale": "x"})
    with patch("axiom.hypothesis_verdict._call_llm", return_value=fake):
        result = run_triage_loop(batch_size=2)
    assert result["processed_count"] == 2
