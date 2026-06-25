"""Phase 5: lineage helpers + parent_strategy_id validation."""


import pytest

from axiom.db import create_strategy_container, get_db
from axiom.hypothesis_lineage import (
    build_canonical_coverage_map,
    build_sibling_table,
)
from axiom.hypotheses import create_hypothesis


def _hyp(idx: int = 0) -> dict:
    return create_hypothesis(
        title=f"H{idx}", market_thesis="m", mechanism="x", why_now=None,
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC"], target_timeframes=["1h"],
    )


def _make_strategy(
    hypothesis_id: str,
    *,
    sid_seed: int,
    symbol: str = "BTC",
    timeframe: str = "1h",
    stage: str = "quick_screen",
    canonical: bool = False,
    parent_strategy_id: str | None = None,
) -> str:
    with get_db() as conn:
        sid, _, _ = create_strategy_container(
            conn,
            name=f"strat-{sid_seed}",
            type_="rsi",
            symbol=symbol,
            timeframe=timeframe,
            params={"regime_filter": "trending"},
            stage=stage,
            hypothesis_id=hypothesis_id,
            strategy_id=f"S{40000 + sid_seed:05d}",
            parent_strategy_id=parent_strategy_id,
        )
        if canonical:
            conn.execute(
                "UPDATE strategies SET canonical = 1 WHERE id = ?",
                (sid,),
            )
        conn.commit()
    return sid


# ---- sibling table ----


def test_sibling_table_returns_active_children(AXIOM_db):
    h = _hyp()
    s1 = _make_strategy(h["id"], sid_seed=1)
    s2 = _make_strategy(h["id"], sid_seed=2, symbol="ETH")
    s3 = _make_strategy(h["id"], sid_seed=3, symbol="SOL", parent_strategy_id=s1)

    table = build_sibling_table(h["id"])
    ids = [row["strategy_id"] for row in table]
    assert s1 in ids and s2 in ids and s3 in ids
    by_id = {row["strategy_id"]: row for row in table}
    assert by_id[s3]["parent_strategy_id"] == s1
    # Bare base assets ("BTC") are repaired to canonical pair form
    # ("BTC/USDT") by ``_normalize_strategy_symbol`` — see
    # ``test_strategy_symbol_normalization``.
    assert by_id[s1]["asset"] == "BTC/USDT"
    assert by_id[s2]["asset"] == "ETH/USDT"
    assert by_id[s1]["regime_filter"] == "trending"


def test_sibling_table_excludes_archived_and_rejected(AXIOM_db):
    h = _hyp()
    keep = _make_strategy(h["id"], sid_seed=1)
    _make_strategy(h["id"], sid_seed=2, stage="archived")
    _make_strategy(h["id"], sid_seed=3, stage="rejected")

    table = build_sibling_table(h["id"])
    assert len(table) == 1
    assert table[0]["strategy_id"] == keep


def test_sibling_table_empty_when_no_children(AXIOM_db):
    h = _hyp()
    assert build_sibling_table(h["id"]) == []


# ---- canonical coverage map ----


def test_canonical_coverage_map_only_counts_canonicals(AXIOM_db):
    h = _hyp()
    _make_strategy(h["id"], sid_seed=1, symbol="BTC")  # not canonical
    s_canon = _make_strategy(h["id"], sid_seed=2, symbol="ETH", canonical=True)
    _make_strategy(h["id"], sid_seed=3, symbol="SOL")  # not canonical

    coverage = build_canonical_coverage_map(h["id"])
    # Bare base assets ("ETH") are repaired to canonical pair form
    # ("ETH/USDT"); coverage map keys it as f"{symbol}:{timeframe}".
    assert "ETH/USDT:1h" in coverage
    assert "BTC/USDT:1h" not in coverage
    assert "SOL/USDT:1h" not in coverage
    assert coverage["ETH/USDT:1h"]["strategy_id"] == s_canon


def test_canonical_coverage_map_empty_for_no_canonicals(AXIOM_db):
    h = _hyp()
    _make_strategy(h["id"], sid_seed=1)
    _make_strategy(h["id"], sid_seed=2, symbol="ETH")
    assert build_canonical_coverage_map(h["id"]) == {}


# ---- create_strategy_container parent validation ----


def test_create_strategy_rejects_parent_from_different_hypothesis(AXIOM_db):
    h_a = _hyp(0)
    h_b = _hyp(1)
    parent = _make_strategy(h_a["id"], sid_seed=1)
    with pytest.raises(ValueError, match="cannot cross hypotheses"):
        with get_db() as conn:
            create_strategy_container(
                conn,
                name="child",
                type_="rsi",
                symbol="BTC",
                timeframe="1h",
                params={},
                stage="quick_screen",
                hypothesis_id=h_b["id"],  # different hypothesis
                parent_strategy_id=parent,
            )


def test_create_strategy_rejects_unknown_parent(AXIOM_db):
    h = _hyp()
    with pytest.raises(ValueError, match="not found"):
        with get_db() as conn:
            create_strategy_container(
                conn,
                name="child",
                type_="rsi",
                symbol="BTC",
                timeframe="1h",
                params={},
                stage="quick_screen",
                hypothesis_id=h["id"],
                parent_strategy_id="S99999_BOGUS",
            )


def test_create_strategy_accepts_parent_in_same_hypothesis(AXIOM_db):
    h = _hyp()
    parent = _make_strategy(h["id"], sid_seed=1)
    child = _make_strategy(h["id"], sid_seed=2, parent_strategy_id=parent)
    table = build_sibling_table(h["id"])
    by_id = {row["strategy_id"]: row for row in table}
    assert by_id[child]["parent_strategy_id"] == parent
