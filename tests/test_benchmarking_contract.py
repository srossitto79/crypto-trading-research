"""Benchmarking-lane research_contract wiring.

The discover_*/inspect_* research tools hard-reject unless the active task's
research_contract has lane='benchmarking' AND external_sources_allowed. Before
this wiring NO code attached a contract, so the harvest arm was unreachable.
"""
from axiom.api_domains.hypotheses import _research_contract_for
from axiom.hypotheses import create_hypothesis
from axiom.research_context import coerce_research_contract


def _benchmarking_crucible() -> dict:
    return create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now=None,
        lane="benchmarking", source_type="operator_seed",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC"], target_timeframes=["1h"],
    )


def test_research_contract_for_benchmarking_unlocks_external_sources(AXIOM_db):
    h = _benchmarking_crucible()
    contract = _research_contract_for(h)
    assert contract["lane"] == "benchmarking"
    assert contract["external_sources_allowed"] is True
    assert "youtube" in contract["allowed_external_source_types"]
    assert "reddit" in contract["allowed_external_source_types"]


def test_contract_survives_runner_coercion(AXIOM_db):
    """The runner re-coerces input_data['research_contract']; the benchmarking
    lane + external access must survive that round-trip (that's the exact gate
    the discover tools read)."""
    h = _benchmarking_crucible()
    coerced = coerce_research_contract(_research_contract_for(h))
    assert coerced.lane == "benchmarking"
    assert coerced.external_sources_allowed is True


def test_non_benchmarking_lane_keeps_external_sources_closed(AXIOM_db):
    h = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now=None,
        lane="exploration", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC"], target_timeframes=["1h"],
    )
    contract = _research_contract_for(h)
    assert contract["lane"] == "exploration"
    assert contract["external_sources_allowed"] is False
