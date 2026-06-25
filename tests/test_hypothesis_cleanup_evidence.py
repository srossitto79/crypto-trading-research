"""Phase 4: time-based evidence rule was removed.

`cleanup_stale_hypotheses` is now a deprecated no-op — age alone is no longer
evidence that a hypothesis should be disproven. The cap + round-robin gates
prevent the unbounded-pool problem that auto-disprove was patching over.

These tests assert the no-op contract so a future re-introduction of
time-based auto-disprove would have to be done deliberately.
"""

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from axiom.api import app
from axiom.db import get_db
from axiom.hypotheses import create_hypothesis


def _age_created_at(hypothesis_id: str, days_ago: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with get_db() as conn:
        conn.execute("UPDATE hypotheses SET created_at = ? WHERE id = ?", (stamp, hypothesis_id))


def _bare_hyp():
    return create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="exploration", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC"], target_timeframes=["1h"],
    )


def test_aged_zero_child_hypothesis_is_NOT_auto_disproven(AXIOM_db):
    """Old hypotheses with no children no longer get flipped to disproven."""
    from axiom.hypothesis_cleanup import cleanup_stale_hypotheses
    hyp = _bare_hyp()
    _age_created_at(hyp["id"], days_ago=20)
    result = cleanup_stale_hypotheses()
    assert result["disproven_count"] == 0
    assert result["ids"] == []
    with get_db() as conn:
        row = conn.execute("SELECT status FROM hypotheses WHERE id=?", (hyp["id"],)).fetchone()
    assert row["status"] == "proposed"


def test_recent_hypothesis_unchanged(AXIOM_db):
    from axiom.hypothesis_cleanup import cleanup_stale_hypotheses
    hyp = _bare_hyp()
    _age_created_at(hyp["id"], days_ago=5)
    result = cleanup_stale_hypotheses()
    assert result["disproven_count"] == 0
    with get_db() as conn:
        row = conn.execute("SELECT status FROM hypotheses WHERE id=?", (hyp["id"],)).fetchone()
    assert row["status"] == "proposed"


def test_endpoint_returns_zero(AXIOM_db):
    """The legacy cleanup endpoint still exists for back-compat but reports 0."""
    hyp = _bare_hyp()
    _age_created_at(hyp["id"], days_ago=30)
    client = TestClient(app)
    r = client.post("/api/hypotheses/cleanup/evidence")
    assert r.status_code == 200
    assert r.json()["disproven_count"] == 0


def test_dry_run_returns_zero_preview(AXIOM_db):
    from axiom.hypothesis_cleanup import cleanup_stale_hypotheses
    hyp = _bare_hyp()
    _age_created_at(hyp["id"], days_ago=30)
    result = cleanup_stale_hypotheses(dry_run=True)
    assert result["would_disprove_count"] == 0
    assert result["ids"] == []
