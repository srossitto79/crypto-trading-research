import pytest
from axiom.db import get_db
from axiom.hypotheses import (
    create_hypothesis,
    update_hypothesis_status,
)

VALID_STATES = {"proposed", "researching", "proven", "disproven"}


def _real_hyp():
    return create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )


def test_update_status_transitions_state(AXIOM_db):
    hyp = _real_hyp()
    memo = {"verdict": "proven", "rationale": "strong signal"}
    updated = update_hypothesis_status(
        hyp["id"], new_status="proven", memo=memo, by="agent:strategy-developer"
    )
    assert updated["status"] == "proven"
    assert updated["verdict_memo"]["verdict"] == "proven"
    assert updated["verdict_memo_by"] == "agent:strategy-developer"


def test_update_status_writes_history_row(AXIOM_db):
    hyp = _real_hyp()
    memo = {"verdict": "disproven", "rationale": "too many failures"}
    update_hypothesis_status(hyp["id"], new_status="disproven", memo=memo, by="cleanup_rule:test")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT payload, written_by FROM hypothesis_verdict_memos WHERE hypothesis_id = ?",
            (hyp["id"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["written_by"] == "cleanup_rule:test"


def test_update_status_history_accumulates(AXIOM_db):
    hyp = _real_hyp()
    update_hypothesis_status(hyp["id"], new_status="researching", memo={"verdict": "researching"}, by="a")
    update_hypothesis_status(hyp["id"], new_status="disproven", memo={"verdict": "disproven"}, by="b")
    update_hypothesis_status(hyp["id"], new_status="researching", memo={"verdict": "researching"}, by="operator")
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM hypothesis_verdict_memos WHERE hypothesis_id = ?",
            (hyp["id"],),
        ).fetchone()["n"]
    assert n == 3


def test_update_status_rejects_invalid_state(AXIOM_db):
    hyp = _real_hyp()
    with pytest.raises(ValueError):
        update_hypothesis_status(hyp["id"], new_status="bogus", memo={}, by="x")


def test_update_status_missing_hypothesis_raises(AXIOM_db):
    with pytest.raises(ValueError):
        update_hypothesis_status("HYP-none", new_status="researching", memo={}, by="x")
