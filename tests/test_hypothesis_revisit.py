"""Phase 7: revisit pass tests."""

from datetime import datetime, timedelta, timezone

import pytest

from axiom.db import get_approval, get_db, kv_set
from axiom.hypotheses import create_hypothesis
from axiom.hypothesis_graduation import graduate_hypothesis
from axiom.hypothesis_revisit import force_revisit, run_revisit_pass


def _hyp(idx: int = 0) -> dict:
    return create_hypothesis(
        title=f"H{idx}", market_thesis="m", mechanism="x", why_now=None,
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC"], target_timeframes=["1h"],
    )


def _set_discipline(**overrides) -> None:
    kv_set(
        "axiom:settings",
        {"research_settings": {"hypothesis_discipline": overrides}},
    )


def _set_next_revisit(hid: str, when: datetime) -> None:
    """Directly push next_revisit_at backward so the pass picks it up."""
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET next_revisit_at = ?, updated_at = ? WHERE id = ?",
            (when.isoformat(), datetime.now(timezone.utc).isoformat(), hid),
        )
        conn.commit()


def _clear_protection(hid: str) -> None:
    """Simulate legacy/unprotected graduated hypotheses for revisit coverage."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE hypotheses
            SET protection_status = 'unprotected',
                protected_at = NULL,
                protected_by = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), hid),
        )
        conn.commit()


def test_run_revisit_pass_promotes_due_hypothesis(AXIOM_db):
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h = _hyp(0)
    graduate_hypothesis(h["id"])
    _clear_protection(h["id"])
    # Push its revisit time into the past
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _set_next_revisit(h["id"], past)

    result = run_revisit_pass()

    assert h["id"] in result["revisited_ids"]
    assert result["skipped_pool_full"] is False
    assert result["evaluated"] >= 1

    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state, status, revisit_count, last_revisited_at, "
            "last_dispatched_at, next_revisit_at "
            "FROM hypotheses WHERE id = ?",
            (h["id"],),
        ).fetchone()
    assert row["manager_state"] == "active"
    assert row["status"] == "researching"
    assert int(row["revisit_count"] or 0) == 1
    assert row["last_revisited_at"] is not None
    assert row["last_dispatched_at"] is None  # cleared so depth gate doesn't block
    # next_revisit_at pushed forward (was in the past; now ~30 days ahead)
    next_at = datetime.fromisoformat(row["next_revisit_at"])
    assert next_at > datetime.now(timezone.utc)


def test_run_revisit_pass_skips_protected_graduated_crucible(AXIOM_db):
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h = _hyp(0)
    graduate_hypothesis(h["id"])
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _set_next_revisit(h["id"], past)

    result = run_revisit_pass()

    assert h["id"] not in result["revisited_ids"]
    assert result["evaluated"] == 1
    assert result["skipped_pool_full"] is False

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT manager_state, status, protection_status, revisit_count,
                   last_revisited_at, next_revisit_at
            FROM hypotheses
            WHERE id = ?
            """,
            (h["id"],),
        ).fetchone()
        approval_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM approvals
            WHERE approval_type = 'crucible_dethrone'
              AND target_type = 'crucible'
              AND target_id = ?
            """,
            (h["id"],),
        ).fetchone()
    assert row["manager_state"] == "graduated"
    assert row["status"] == "proven"
    assert row["protection_status"] == "protected"
    assert int(row["revisit_count"] or 0) == 0
    assert row["last_revisited_at"] is None
    assert datetime.fromisoformat(row["next_revisit_at"]) < datetime.now(timezone.utc)
    assert int(approval_count["n"] or 0) == 0


def test_run_revisit_pass_skips_if_not_due(AXIOM_db):
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h = _hyp(0)
    graduate_hypothesis(h["id"])
    # next_revisit_at is ~30 days out by default — not due

    result = run_revisit_pass()

    assert h["id"] not in result["revisited_ids"]
    assert result["evaluated"] == 0


def test_run_revisit_pass_stops_on_pool_full(AXIOM_db):
    # Raise cap first so we can set up the state, then lower it to simulate "full"
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    _hyp(0)  # active
    _hyp(1)  # active
    h_g = _hyp(2)
    graduate_hypothesis(h_g["id"])
    _clear_protection(h_g["id"])
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _set_next_revisit(h_g["id"], past)

    # Now set cap=2 so with 2 active hypotheses, pool is "full"
    _set_discipline(active_pool_cap=2, revisit_interval_days=30)

    result = run_revisit_pass()

    assert h_g["id"] not in result["revisited_ids"]
    assert result["skipped_pool_full"] is True
    assert result["evaluated"] == 1

    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state FROM hypotheses WHERE id = ?",
            (h_g["id"],),
        ).fetchone()
    assert row["manager_state"] == "graduated"  # unchanged


def test_run_revisit_pass_respects_cap_mid_pass(AXIOM_db):
    """With cap=3 and 2 active, only one graduated can be promoted per pass."""
    _set_discipline(active_pool_cap=3, revisit_interval_days=30)
    _hyp(0)  # active
    _hyp(1)  # active

    # Graduate two hypotheses and make both due
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h_a = _hyp(2)
    h_b = _hyp(3)
    graduate_hypothesis(h_a["id"])
    graduate_hypothesis(h_b["id"])
    _clear_protection(h_a["id"])
    _clear_protection(h_b["id"])
    past_a = datetime.now(timezone.utc) - timedelta(days=2)
    past_b = datetime.now(timezone.utc) - timedelta(days=1)
    _set_next_revisit(h_a["id"], past_a)  # older — should be picked first
    _set_next_revisit(h_b["id"], past_b)

    # Lower cap back to 3 so only 1 slot is free
    _set_discipline(active_pool_cap=3, revisit_interval_days=30)

    result = run_revisit_pass()

    assert len(result["revisited_ids"]) == 1
    assert result["revisited_ids"][0] == h_a["id"]  # older first
    assert result["skipped_pool_full"] is True
    assert result["evaluated"] == 2


def test_force_revisit_succeeds(AXIOM_db):
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h = _hyp(0)
    graduate_hypothesis(h["id"])
    _clear_protection(h["id"])

    result = force_revisit(h["id"])
    assert result["hypothesis_id"] == h["id"]
    assert result["manager_state"] == "active"
    assert result["status"] == "researching"

    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state, status, revisit_count "
            "FROM hypotheses WHERE id = ?",
            (h["id"],),
        ).fetchone()
    assert row["manager_state"] == "active"
    assert row["status"] == "researching"
    assert int(row["revisit_count"] or 0) == 1


def test_force_revisit_protected_graduated_crucible_requires_approval(AXIOM_db):
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h = _hyp(0)
    graduate_hypothesis(h["id"])

    result = force_revisit(h["id"])
    second = force_revisit(h["id"])
    approval = get_approval(result["approval_id"])

    assert result["hypothesis_id"] == h["id"]
    assert result["approval_required"] is True
    assert second["approval_id"] == result["approval_id"]
    assert approval is not None
    assert approval["approval_type"] == "crucible_dethrone"
    assert approval["target_id"] == h["id"]
    assert approval["requested_status"] == "researching"
    assert approval["payload"]["new_evidence"]["requested_status"] == "researching"
    assert approval["payload"]["new_evidence"]["requested_manager_state"] == "active"
    assert approval["payload"]["recommended_action"] == "dethrone/revisit"

    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state, status, protection_status, revisit_count "
            "FROM hypotheses WHERE id = ?",
            (h["id"],),
        ).fetchone()
    assert row["manager_state"] == "graduated"
    assert row["status"] == "proven"
    assert row["protection_status"] == "contested"
    assert int(row["revisit_count"] or 0) == 0


def test_force_revisit_raises_value_error_when_not_graduated(AXIOM_db):
    _set_discipline(active_pool_cap=10)
    h = _hyp(0)
    # h is still active — not eligible for revisit
    with pytest.raises(ValueError, match="graduated"):
        force_revisit(h["id"])


def test_force_revisit_raises_value_error_for_unknown_id(AXIOM_db):
    with pytest.raises(ValueError, match="unknown"):
        force_revisit("does-not-exist")


def test_force_revisit_evicts_weakest_when_pool_full(AXIOM_db):
    """When the pool is at cap, force_revisit evicts the weakest instead of refusing.

    Mirrors the pressure-valve semantics of create_hypothesis — operators
    re-activating a graduated hypothesis are never refused.
    """
    from axiom.db import get_db

    _set_discipline(active_pool_cap=2, revisit_interval_days=30)
    a = _hyp(0)
    _hyp(1)

    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h_g = _hyp(2)
    graduate_hypothesis(h_g["id"])
    _clear_protection(h_g["id"])

    # Shrink cap so pool is "full"
    _set_discipline(active_pool_cap=2, revisit_interval_days=30)

    revived = force_revisit(h_g["id"])
    assert revived["manager_state"] == "active"
    assert revived["status"] == "researching"

    # The weakest active (a — oldest updated_at, 0 strategies) was evicted
    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state FROM hypotheses WHERE id = ?", (a["id"],)
        ).fetchone()
        assert row["manager_state"] == "archived"


def test_force_revisit_raises_when_no_eviction_possible(AXIOM_db, monkeypatch):
    """Defensive fallback: RuntimeError only if no eviction victim exists."""
    _set_discipline(active_pool_cap=10, revisit_interval_days=30)
    h_g = _hyp(0)
    graduate_hypothesis(h_g["id"])
    _clear_protection(h_g["id"])

    _set_discipline(active_pool_cap=1, revisit_interval_days=30)
    _hyp(1)  # occupies the single slot

    import axiom.hypotheses as hyp_module

    monkeypatch.setattr(
        hyp_module, "_pick_weakest_active_hypothesis", lambda conn, protect_ids=(): None
    )
    with pytest.raises(RuntimeError, match="no eviction victim"):
        force_revisit(h_g["id"])


def test_revisit_does_not_count_disproven_in_active_cap(AXIOM_db):
    """Disproven hypotheses don't occupy the cap — a graduated one can still revive."""
    from axiom.hypotheses import update_hypothesis_status

    _set_discipline(active_pool_cap=3, revisit_interval_days=30)
    h_active = _hyp(0)  # active
    h_disp = _hyp(1)  # will disprove
    update_hypothesis_status(
        h_disp["id"],
        new_status="disproven",
        memo={"verdict": "disproven", "rationale": "test"},
        by="test",
    )

    h_g = _hyp(2)
    graduate_hypothesis(h_g["id"])
    _clear_protection(h_g["id"])
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _set_next_revisit(h_g["id"], past)

    result = run_revisit_pass()

    # Only 1 active (h_active) counts against cap=3 → revisit succeeds
    assert h_g["id"] in result["revisited_ids"]
    assert result["skipped_pool_full"] is False
