from axiom.crucibles import get_crucible, mark_crucible_viable
from axiom.db import get_approval, get_db, kv_set
from axiom.hypotheses import (
    archive_hypothesis,
    bulk_trash_hypotheses,
    create_hypothesis,
    trash_hypothesis,
    update_hypothesis_status,
)


def _make_crucible(idx: int = 1) -> dict:
    return create_hypothesis(
        title=f"Crucible {idx}",
        market_thesis="A direct, testable market thesis.",
        mechanism="A measurable market mechanism.",
        why_now="Fresh market condition.",
        lane="benchmarking",
        source_type="agent_original",
        origin_agent_id="research-agent",
        origin_role="quant-researcher",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )


def test_archive_unprotected_crucible_still_archives(AXIOM_db):
    crucible = _make_crucible()

    archived = archive_hypothesis(crucible["id"])

    assert archived["manager_state"] == "archived"
    assert archived.get("approval_required") is not True


def test_archive_protected_crucible_creates_approval_instead(AXIOM_db):
    crucible = _make_crucible()
    mark_crucible_viable(
        crucible["id"],
        evidence_id="BT-900",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.8},
    )

    result = archive_hypothesis(crucible["id"])
    reloaded = get_crucible(crucible["id"])
    approval = get_approval(result["approval_id"])

    assert result["approval_required"] is True
    assert result["manager_state"] != "archived"
    assert result["protection_status"] == "contested"
    assert reloaded["protection_status"] == "contested"
    assert approval is not None
    assert approval["approval_type"] == "crucible_dethrone"
    assert approval["target_id"] == crucible["id"]


def test_trash_protected_crucible_creates_approval_with_trash_intent(AXIOM_db):
    crucible = _make_crucible()
    mark_crucible_viable(
        crucible["id"],
        evidence_id="BT-901",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.81},
    )

    result = trash_hypothesis(crucible["id"])
    approval = get_approval(result["approval_id"])

    assert result["approval_required"] is True
    assert result["manager_state"] != "trash"
    assert approval is not None
    assert approval["requested_status"] == "trash"
    assert approval["payload"]["new_evidence"]["requested_manager_state"] == "trash"
    assert approval["payload"]["recommended_action"] == "dethrone/trash"


def test_status_demotion_of_protected_crucible_creates_approval_instead(AXIOM_db):
    crucible = _make_crucible()
    mark_crucible_viable(
        crucible["id"],
        evidence_id="BT-904",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.84},
    )

    result = update_hypothesis_status(
        crucible["id"],
        new_status="disproven",
        memo={"reason": "latest validation failed"},
        by="cleanup-rule",
    )
    reloaded = get_crucible(crucible["id"])
    approval = get_approval(result["approval_id"])

    assert result["approval_required"] is True
    assert result["status"] == "proven"
    assert result["protection_status"] == "contested"
    assert reloaded["status"] == "proven"
    assert reloaded["protection_status"] == "contested"
    assert approval is not None
    assert approval["approval_type"] == "crucible_dethrone"
    assert approval["requested_status"] == "disproven"
    assert approval["payload"]["new_evidence"]["requested_status"] == "disproven"


def test_status_demotion_of_unprotected_crucible_still_updates(AXIOM_db):
    crucible = _make_crucible()

    result = update_hypothesis_status(
        crucible["id"],
        new_status="disproven",
        memo={"reason": "baseline failed"},
        by="cleanup-rule",
    )

    assert result["status"] == "disproven"
    assert result["protection_status"] == "unprotected"
    assert result.get("approval_required") is not True


def test_bulk_trash_mixed_unprotected_and_protected_returns_approval(AXIOM_db):
    unprotected = _make_crucible(1)
    protected = _make_crucible(2)
    mark_crucible_viable(
        protected["id"],
        evidence_id="BT-902",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.82},
    )

    results = bulk_trash_hypotheses([unprotected["id"], protected["id"]])
    by_id = {result["id"]: result for result in results}
    approval = get_approval(by_id[protected["id"]]["approval_id"])

    assert by_id[unprotected["id"]]["manager_state"] == "trash"
    assert by_id[protected["id"]]["approval_required"] is True
    assert by_id[protected["id"]]["manager_state"] != "trash"
    assert approval is not None
    assert approval["requested_status"] == "trash"


def test_repeated_archive_and_trash_reuse_pending_approval(AXIOM_db):
    crucible = _make_crucible()
    mark_crucible_viable(
        crucible["id"],
        evidence_id="BT-903",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.83},
    )

    archive_result = archive_hypothesis(crucible["id"])
    trash_result = trash_hypothesis(crucible["id"])
    approval = get_approval(trash_result["approval_id"])

    assert archive_result["approval_id"] == trash_result["approval_id"]
    assert approval is not None
    assert approval["requested_status"] == "trash"
    assert approval["payload"]["new_evidence"]["requested_manager_state"] == "trash"
    assert approval["payload"]["recommended_action"] == "dethrone/trash"


def test_active_pool_eviction_skips_protected_crucibles(AXIOM_db):
    kv_set(
        "axiom:settings",
        {"research_settings": {"hypothesis_discipline": {"active_pool_cap": 2}}},
    )
    protected = _make_crucible(1)
    unprotected = _make_crucible(2)
    mark_crucible_viable(
        protected["id"],
        evidence_id="BT-1",
        by="verdict-loop",
        evidence_packet={"hit_rate": 1.0},
    )
    with get_db() as conn:
        conn.execute(
            "UPDATE hypotheses SET status = 'researching', manager_state = 'active' WHERE id = ?",
            (protected["id"],),
        )

    new_crucible = _make_crucible(3)

    with get_db() as conn:
        states = {
            row["id"]: row["manager_state"]
            for row in conn.execute("SELECT id, manager_state FROM hypotheses").fetchall()
        }
    assert states[protected["id"]] == "active"
    assert states[unprotected["id"]] == "archived"
    assert states[new_crucible["id"]] == "active"
