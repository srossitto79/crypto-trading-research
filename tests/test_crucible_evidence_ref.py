"""evidence-ref-on-proven: a proven crucible always carries an evidence reference
(audit item 21) — never NULL initial_viability_evidence_id."""
import json
from unittest.mock import patch

import pytest

from axiom.crucibles import mark_crucible_viable
from axiom.db import get_db, kv_set
from axiom.hypotheses import create_hypothesis, get_hypothesis


def _hyp() -> dict:
    return create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now=None,
        lane="benchmarking", source_type="agent_original", origin_agent_id="a",
        origin_role="strategy-developer", target_assets=["BTC"], target_timeframes=["1h"],
    )


def test_mark_crucible_viable_requires_evidence_id(AXIOM_db):
    h = _hyp()
    with pytest.raises(ValueError):
        mark_crucible_viable(h["id"], evidence_id="", by="x")
    mark_crucible_viable(h["id"], evidence_id="BT-900", by="x")
    assert get_hypothesis(h["id"])["initial_viability_evidence_id"] == "BT-900"


def test_proven_verdict_never_leaves_evidence_ref_null(AXIOM_db):
    kv_set("axiom:settings", {"research_settings": {"hypothesis_discipline": {
        "verdict_hit_rate_threshold": 0.4, "verdict_min_diversity_cells": 1, "verdict_rolling_window": 4,
    }}})
    from axiom.hypothesis_verdict import write_verdict_memo

    h = _hyp()  # 1 declared cell -> proportional gate effective_min=1
    with get_db() as conn:
        for i in range(2):  # paper-stage passing children
            conn.execute(
                """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
                   stage, status, hypothesis_id, owner, params, metrics, verdict, created_at, updated_at)
                   VALUES (?, ?, 'n', 'rsi', 'BTC', '1h', 'paper', 'active', ?, 'brain',
                           '{}', '{}', '{}', datetime('now'), datetime('now'))""",
                (f"SP{i}", f"SP{i}", h["id"]),
            )
    # LLM verdict carries NO evidence_id -> the proven path must synthesize a fallback.
    with patch("axiom.hypothesis_verdict._call_llm", return_value=json.dumps({"verdict": "proven", "rationale": "r"})):
        result = write_verdict_memo(h["id"])
    assert result["hypothesis"]["status"] == "proven"
    assert get_hypothesis(h["id"])["initial_viability_evidence_id"] is not None
