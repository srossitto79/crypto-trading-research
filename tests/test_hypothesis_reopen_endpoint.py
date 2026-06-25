from fastapi.testclient import TestClient
from axiom.api import app
from axiom.hypotheses import create_hypothesis, update_hypothesis_status


def test_reopen_flips_disproven_to_researching(AXIOM_db):
    hyp = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="operator_seed",
        origin_agent_id=None, origin_role="operator",
        target_assets=["unspecified"], target_timeframes=["unspecified"],
    )
    update_hypothesis_status(
        hyp["id"], new_status="disproven",
        memo={"verdict": "disproven", "rationale": "LLM said so"},
        by="agent:strategy-developer",
    )
    client = TestClient(app)
    r = client.post(
        f"/api/hypotheses/{hyp['id']}/reopen",
        json={"rationale": "operator disagrees, try again with new data"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["hypothesis"]["status"] == "researching"
    assert body["hypothesis"]["verdict_memo"]["verdict"] == "researching"
    assert body["hypothesis"]["verdict_memo_by"] == "operator"


def test_reopen_nonexistent_hypothesis_404(AXIOM_db):
    client = TestClient(app)
    r = client.post("/api/hypotheses/HYP-nope/reopen", json={})
    assert r.status_code == 404


def test_reopen_already_researching_is_noop_with_200(AXIOM_db):
    """Reopening a non-disproven hypothesis should not error — idempotent."""
    hyp = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )
    client = TestClient(app)
    r = client.post(f"/api/hypotheses/{hyp['id']}/reopen", json={})
    assert r.status_code == 200
    assert r.json()["hypothesis"]["status"] in {"researching", "proposed"}
