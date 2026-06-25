import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from axiom.api import app
from axiom.db import get_db, kv_set
from axiom.hypotheses import create_hypothesis


def _seed_passing_children(hypothesis_id: str, n: int) -> None:
    """Seed n passing children across distinct cells so the math floor
    supports 'proven'."""
    cells = [("BTC", "1h"), ("ETH", "1h"), ("SOL", "1h"), ("AVAX", "1h")]
    with get_db() as conn:
        for i in range(n):
            sym, tf = cells[i % len(cells)]
            sid = f"S_VERDICT_{i}"
            conn.execute(
                """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
                   stage, status, hypothesis_id, owner, params, metrics, verdict,
                   created_at, updated_at)
                   VALUES (?, ?, 'n', 'rsi', ?, ?, 'paper', 'active', ?, 'brain',
                           '{}', '{}', '{}', datetime('now'), datetime('now'))""",
                (sid, sid, sym, tf, hypothesis_id),
            )


def test_verdict_endpoint_triggers_writer(AXIOM_db):
    # Lower thresholds so the math floor allows 'proven' with our seed
    kv_set(
        "axiom:settings",
        {"research_settings": {"hypothesis_discipline": {
            "verdict_hit_rate_threshold": 0.5,
            "verdict_min_diversity_cells": 2,
            "verdict_rolling_window": 4,
        }}},
    )
    hyp = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )
    _seed_passing_children(hyp["id"], n=4)
    fake = json.dumps({"verdict": "proven", "rationale": "good"})
    with patch("axiom.hypothesis_verdict._call_llm", return_value=fake):
        client = TestClient(app)
        r = client.post(f"/api/hypotheses/{hyp['id']}/verdict")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["hypothesis"]["status"] == "proven"


def test_verdict_endpoint_falls_back_to_math_floor_on_llm_failure(AXIOM_db):
    """When the LLM auditor is unavailable, the verdict must NOT freeze the
    pipeline — it falls back to the deterministic mathematical floor and still
    advances the hypothesis (ok=True), flagging the memo llm_unavailable. The
    auditor only ever DOWNGRADES the floor, so applying the floor alone is safe.
    """
    hyp = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now="n",
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )
    with patch("axiom.hypothesis_verdict._call_llm", side_effect=RuntimeError("boom")):
        client = TestClient(app)
        r = client.post(f"/api/hypotheses/{hyp['id']}/verdict")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # The resolved verdict equals the precomputed mathematical floor (no children
    # seeded -> 'researching'), and the hypothesis status reflects it.
    floor = body["signals"]["mathematical_verdict"]
    assert body["hypothesis"]["status"] == floor


def test_verdict_endpoint_missing_hypothesis_404(AXIOM_db):
    client = TestClient(app)
    r = client.post("/api/hypotheses/HYP-missing/verdict")
    assert r.status_code == 404
