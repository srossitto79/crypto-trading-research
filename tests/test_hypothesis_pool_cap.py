"""Active-pool pressure-valve semantics.

The active_pool_cap is a *target population size*, not a hard gate. When the pool
is at cap, create_hypothesis auto-archives the weakest active hypothesis (fewest
linked strategies, then stalest) and admits the new one. HypothesisPoolFullError
is only raised as a defensive fallback if no eviction victim can be found.
"""

import time

import pytest

from axiom.db import get_db, kv_set
from axiom.hypotheses import (
    HypothesisPoolFullError,
    create_hypothesis,
    update_hypothesis_status,
)


def _make_hyp(idx: int) -> dict:
    return create_hypothesis(
        title=f"H{idx}",
        market_thesis=f"thesis {idx}",
        mechanism="m",
        why_now=None,
        lane="benchmarking",
        source_type="agent_original",
        origin_agent_id="a",
        origin_role="strategy-developer",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )


def _set_cap(cap: int) -> None:
    kv_set(
        "axiom:settings",
        {"research_settings": {"hypothesis_discipline": {"active_pool_cap": cap}}},
    )


def _count_active(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM hypotheses "
        "WHERE manager_state = 'active' "
        "AND status NOT IN ('disproven', 'proven')"
    ).fetchone()
    return int(row["n"] or 0)


def _manager_state(conn, hypothesis_id: str) -> str:
    row = conn.execute(
        "SELECT manager_state FROM hypotheses WHERE id = ?", (hypothesis_id,)
    ).fetchone()
    return str(row["manager_state"])


def test_create_hypothesis_succeeds_below_cap(AXIOM_db):
    _set_cap(5)
    for i in range(4):
        _make_hyp(i)
    # 5th still fits without eviction
    hyp = _make_hyp(99)
    assert hyp["id"]
    with get_db() as conn:
        assert _count_active(conn) == 5


def test_at_cap_evicts_weakest_and_admits_new(AXIOM_db):
    """When pool is full, the weakest hypothesis is archived to make room."""
    _set_cap(3)
    earliest = _make_hyp(0)
    time.sleep(0.01)
    middle = _make_hyp(1)
    time.sleep(0.01)
    latest = _make_hyp(2)

    # All three have 0 linked strategies — tiebreak falls to oldest updated_at,
    # which is `earliest`.
    new = _make_hyp(99)
    assert new["id"]

    with get_db() as conn:
        # Active count still at cap (one out, one in)
        assert _count_active(conn) == 3
        # `earliest` was evicted
        assert _manager_state(conn, earliest["id"]) == "archived"
        assert _manager_state(conn, middle["id"]) == "active"
        assert _manager_state(conn, latest["id"]) == "active"
        assert _manager_state(conn, new["id"]) == "active"


def test_eviction_prefers_fewest_strategies_over_age(AXIOM_db):
    """Strategy count dominates updated_at for eviction priority."""
    _set_cap(3)
    oldest = _make_hyp(0)
    time.sleep(0.01)
    populated = _make_hyp(1)
    time.sleep(0.01)
    newest = _make_hyp(2)

    # Give `oldest` a linked strategy so it's no longer the weakest by count.
    # `populated` and `newest` have 0 strategies; tiebreak on updated_at picks
    # `populated` as the victim (older of the two).
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (id, name, symbol, timeframe, params, hypothesis_id, status, stage)
            VALUES (?, 'S1', 'BTC-PERP', '1h', '{}', ?, 'proposed', 'quick_screen')
            """,
            (f"STRAT-{oldest['id']}", oldest["id"]),
        )
        conn.commit()

    _make_hyp(99)

    with get_db() as conn:
        assert _manager_state(conn, oldest["id"]) == "active"  # protected by 1 strat
        assert _manager_state(conn, populated["id"]) == "archived"  # weakest now
        assert _manager_state(conn, newest["id"]) == "active"


def test_disproven_hypotheses_do_not_count_against_cap(AXIOM_db):
    _set_cap(5)
    five = [_make_hyp(i) for i in range(5)]
    update_hypothesis_status(
        five[0]["id"], new_status="disproven", memo={"verdict": "disproven"}, by="t"
    )
    update_hypothesis_status(
        five[1]["id"], new_status="disproven", memo={"verdict": "disproven"}, by="t"
    )
    # Two slots freed by disproven; can create two more without eviction
    a = _make_hyp(101)
    b = _make_hyp(102)
    with get_db() as conn:
        assert _manager_state(conn, a["id"]) == "active"
        assert _manager_state(conn, b["id"]) == "active"
        # Disproven ones still manager_state=active but excluded by status filter
        assert _manager_state(conn, five[0]["id"]) == "active"


def test_proven_hypotheses_do_not_count_against_cap(AXIOM_db):
    _set_cap(3)
    a, _b, _c = (_make_hyp(i) for i in range(3))
    update_hypothesis_status(
        a["id"], new_status="proven", memo={"verdict": "proven"}, by="t"
    )
    # One slot freed — can create another without eviction
    new = _make_hyp(50)
    with get_db() as conn:
        assert _manager_state(conn, new["id"]) == "active"


def test_archived_hypotheses_do_not_count_against_cap(AXIOM_db):
    _set_cap(3)
    a, _b, _c = (_make_hyp(i) for i in range(3))
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET manager_state = 'archived' WHERE id = ?",
            (a["id"],),
        )
        conn.commit()
    # One slot freed; no eviction needed
    _make_hyp(99)
    with get_db() as conn:
        assert _count_active(conn) == 3


def test_default_cap_is_one_hundred(AXIOM_db):
    """Default cap is 100 — floodgates open."""
    from axiom.research_contract import get_hypothesis_discipline_settings

    discipline = get_hypothesis_discipline_settings()
    assert discipline["active_pool_cap"] == 100


def test_derived_from_parent_is_protected_from_eviction(AXIOM_db):
    """A derived_from parent never gets evicted to admit its own child."""
    _set_cap(2)
    parent = _make_hyp(0)
    time.sleep(0.01)
    sibling = _make_hyp(1)

    # Creating a derived-from-parent hypothesis must NOT evict the parent;
    # it should evict the sibling instead.
    child = create_hypothesis(
        title="child",
        market_thesis="derived thesis",
        mechanism="m",
        why_now=None,
        lane="benchmarking",
        source_type="agent_original",
        origin_agent_id="a",
        origin_role="strategy-developer",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
        derived_from_hypothesis_id=parent["id"],
    )
    assert child["id"]
    with get_db() as conn:
        assert _manager_state(conn, parent["id"]) == "active"
        assert _manager_state(conn, sibling["id"]) == "archived"
        assert _manager_state(conn, child["id"]) == "active"


def test_protected_hypothesis_is_not_pool_pressure_victim(AXIOM_db):
    _set_cap(2)
    protected = _make_hyp(0)
    victim = _make_hyp(1)

    with get_db() as conn:
        conn.execute(
            """
            UPDATE hypotheses
            SET status = 'researching',
                protection_status = 'protected',
                protected_at = '2026-04-24T00:00:00+00:00',
                protected_by = 'test'
            WHERE id = ?
            """,
            (protected["id"],),
        )

    _make_hyp(99)

    with get_db() as conn:
        assert _manager_state(conn, protected["id"]) == "active"
        assert _manager_state(conn, victim["id"]) == "archived"


def test_pool_full_error_still_raised_if_no_eviction_possible(AXIOM_db, monkeypatch):
    """Defensive fallback: if the eviction picker returns None, raise the error."""
    _set_cap(1)
    _make_hyp(0)

    import axiom.hypotheses as hyp_module

    monkeypatch.setattr(
        hyp_module, "_pick_weakest_active_hypothesis", lambda conn, protect_ids=(): None
    )
    with pytest.raises(HypothesisPoolFullError) as exc_info:
        _make_hyp(1)
    assert exc_info.value.cap == 1
    assert exc_info.value.active_count == 1


def test_pool_full_error_if_defensive_eviction_does_not_archive(AXIOM_db, monkeypatch):
    """If a picked victim becomes protected, creation must not pretend eviction happened."""
    _set_cap(1)
    protected = _make_hyp(0)
    with get_db() as conn:
        conn.execute(
            """
            UPDATE hypotheses
            SET protection_status = 'protected',
                protected_at = '2026-04-24T00:00:00+00:00',
                protected_by = 'test'
            WHERE id = ?
            """,
            (protected["id"],),
        )

    import axiom.hypotheses as hyp_module

    monkeypatch.setattr(
        hyp_module,
        "_pick_weakest_active_hypothesis",
        lambda conn, protect_ids=(): {
            "id": protected["id"],
            "display_id": protected["display_id"],
            "strategy_count": 0,
        },
    )

    with pytest.raises(HypothesisPoolFullError):
        _make_hyp(1)

    with get_db() as conn:
        assert _manager_state(conn, protected["id"]) == "active"
        assert _count_active(conn) == 1
