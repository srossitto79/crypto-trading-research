import pytest

from axiom.crucibles import (
    get_crucible,
    is_crucible_protected,
    mark_crucible_viable,
    request_dethrone_approval,
)
from axiom.db import _backfill_proven_hypothesis_protection, get_approval, get_db
from axiom.hypothesis_graduation import graduate_hypothesis
from axiom.hypotheses import create_hypothesis, update_hypothesis_status


def _make_crucible() -> dict:
    return create_hypothesis(
        title="Breakout after funding reset",
        market_thesis="Crowded shorts unwind after funding resets below zero.",
        mechanism="Funding compression plus price reclaim creates reflexive buying.",
        why_now="Funding rates recently crossed negative on liquid majors.",
        lane="benchmarking",
        source_type="agent_original",
        origin_agent_id="research-agent",
        origin_role="quant-researcher",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )


def test_created_hypothesis_has_default_crucible_protection_fields(AXIOM_db):
    hypothesis = _make_crucible()

    crucible = get_crucible(hypothesis["id"])

    assert crucible is not None
    assert crucible["crucible_id"] == hypothesis["id"]
    assert crucible["status"] == "proposed"
    assert crucible["crucible_status"] == "proposed"
    assert crucible["protection_status"] == "unprotected"
    assert crucible["protected_at"] is None
    assert is_crucible_protected(crucible) is False


def test_mark_crucible_viable_sets_protection_fields(AXIOM_db):
    hypothesis = _make_crucible()

    updated = mark_crucible_viable(
        hypothesis["id"],
        evidence_id="BT-123",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.75, "diversity_cells": 3},
    )

    assert updated["status"] == "proven"
    assert updated["crucible_status"] == "viable"
    assert updated["protection_status"] == "protected"
    assert updated["protected_at"]
    assert updated["protected_by"] == "verdict-loop"
    assert updated["initial_viability_evidence_id"] == "BT-123"
    assert is_crucible_protected(updated) is True


def test_update_hypothesis_status_to_proven_protects_crucible(AXIOM_db):
    hypothesis = _make_crucible()

    update_hypothesis_status(
        hypothesis["id"],
        new_status="proven",
        memo={"evidence_id": "BT-456", "hit_rate": 0.82},
        by="verdict-writer",
    )

    crucible = get_crucible(hypothesis["id"])

    assert crucible["status"] == "proven"
    assert crucible["crucible_status"] == "viable"
    assert crucible["protection_status"] == "protected"
    assert crucible["protected_at"]
    assert crucible["protected_by"] == "verdict-writer"
    assert crucible["initial_viability_evidence_id"] == "BT-456"


def test_graduate_hypothesis_protects_viable_crucible(AXIOM_db):
    hypothesis = _make_crucible()

    graduate_hypothesis(hypothesis["id"])

    crucible = get_crucible(hypothesis["id"])

    assert crucible["status"] == "proven"
    assert crucible["crucible_status"] == "viable"
    assert crucible["protection_status"] == "protected"
    assert crucible["protected_at"]
    assert crucible["protected_by"] == "graduation"


def test_migration_backfills_existing_proven_crucibles_as_protected(AXIOM_db):
    hypothesis = _make_crucible()

    with get_db() as conn:
        conn.execute(
            """
            UPDATE hypotheses
            SET status = 'proven',
                protection_status = 'unprotected',
                verdict_memo = ?,
                verdict_memo_at = ?,
                verdict_memo_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                '{"evidence_id":"BT-789","hit_rate":0.91}',
                "2026-04-24T10:00:00+00:00",
                "legacy-verdict-loop",
                "2026-04-24T10:01:00+00:00",
                hypothesis["id"],
            ),
        )

    with get_db() as conn:
        _backfill_proven_hypothesis_protection(conn, "2026-04-24T10:02:00+00:00")
    crucible = get_crucible(hypothesis["id"])

    assert crucible["protection_status"] == "protected"
    assert crucible["protected_at"] == "2026-04-24T10:00:00+00:00"
    assert crucible["protected_by"] == "legacy-verdict-loop"
    assert crucible["initial_viability_evidence_id"] == "BT-789"


def test_request_dethrone_approval_marks_contested_and_creates_approval(AXIOM_db):
    hypothesis = _make_crucible()
    mark_crucible_viable(
        hypothesis["id"],
        evidence_id="BT-123",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.75},
    )

    approval_id = request_dethrone_approval(
        hypothesis["id"],
        actor="risk-manager",
        reason="Recent revalidation failed across the primary market scope.",
        new_evidence={"hit_rate": 0.0, "sample": 6},
        recommended_action="dethrone/archive",
    )

    approval = get_approval(approval_id)
    crucible = get_crucible(hypothesis["id"])

    assert approval is not None
    assert approval["approval_type"] == "crucible_dethrone"
    assert approval["target_type"] == "crucible"
    assert approval["target_id"] == hypothesis["id"]
    assert approval["requested_status"] == "archived"
    assert approval["payload"]["recommended_action"] == "dethrone/archive"
    assert approval["payload"]["initial_viability_evidence_id"] == "BT-123"
    assert approval["payload"]["current_verdict_memo"]["evidence_id"] == "BT-123"
    assert approval["payload"]["current_verdict_memo"]["evidence_packet"]["hit_rate"] == 0.75
    assert crucible["protection_status"] == "contested"
    assert crucible["contested_at"]


def test_request_dethrone_approval_reuses_existing_pending_approval(AXIOM_db):
    hypothesis = _make_crucible()
    mark_crucible_viable(
        hypothesis["id"],
        evidence_id="BT-123",
        by="verdict-loop",
        evidence_packet={"hit_rate": 0.75},
    )

    first_approval_id = request_dethrone_approval(
        hypothesis["id"],
        actor="risk-manager",
        reason="Recent revalidation failed.",
        new_evidence={"hit_rate": 0.0},
    )
    second_approval_id = request_dethrone_approval(
        hypothesis["id"],
        actor="risk-manager",
        reason="Same dethrone request retried.",
        new_evidence={"hit_rate": 0.0},
    )

    with get_db() as conn:
        approval_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM approvals
            WHERE approval_type = 'crucible_dethrone'
              AND target_type = 'crucible'
              AND target_id = ?
            """,
            (hypothesis["id"],),
        ).fetchone()[0]

    assert second_approval_id == first_approval_id
    assert approval_count == 1


def test_request_dethrone_approval_rejects_unprotected_crucible(AXIOM_db):
    hypothesis = _make_crucible()

    with pytest.raises(ValueError, match="requires a viable protected crucible"):
        request_dethrone_approval(
            hypothesis["id"],
            actor="risk-manager",
            reason="Cannot dethrone an unproven crucible.",
        )

    crucible = get_crucible(hypothesis["id"])
    with get_db() as conn:
        approval_count = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]

    assert crucible["status"] == "proposed"
    assert crucible["protection_status"] == "unprotected"
    assert crucible["contested_at"] is None
    assert approval_count == 0


def test_strategy_table_has_crucible_provenance_columns(AXIOM_db):
    with get_db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(strategies)").fetchall()}

    assert "origin_crucible_id" in columns
    assert "origin_agent_id" in columns
    assert "origin_task_id" in columns
    assert "origin_model" in columns


def test_derive_crucible_status_lifecycle():
    """Derived user-facing lifecycle: proposed->testing->viable->expanded (+failed)."""
    from axiom.crucibles import EXPANDED_MIN_STRATEGIES, derive_crucible_status

    assert derive_crucible_status(status="proposed") == "proposed"
    assert derive_crucible_status(status="researching") == "testing"
    assert derive_crucible_status(status="disproven") == "failed"
    # Proven with modest coverage stays viable...
    assert derive_crucible_status(status="proven", strategy_count=1) == "viable"
    # ...but a family of candidates makes it expanded.
    assert (
        derive_crucible_status(status="proven", strategy_count=EXPANDED_MIN_STRATEGIES)
        == "expanded"
    )
    # A single promoted (paper/live) descendant also makes it expanded.
    assert (
        derive_crucible_status(status="proven", strategy_count=1, has_promoted_descendant=True)
        == "expanded"
    )
    # The expanded upgrade only applies to a viable/proven base.
    assert (
        derive_crucible_status(status="researching", strategy_count=99) == "testing"
    )


def test_detail_and_summary_payloads_expose_crucible_status_and_protection(AXIOM_db):
    """1a: API payloads surface the derived lifecycle + protection fields."""
    from axiom.api_domains.hypotheses import (
        get_hypothesis_detail_payload,
        list_hypotheses_summary,
    )
    from axiom.crucibles import mark_crucible_viable
    from axiom.hypotheses import create_hypothesis

    hyp = create_hypothesis(
        title="Expanded thesis",
        market_thesis="m",
        mechanism="x",
        why_now=None,
        lane="benchmarking",
        source_type="agent_original",
        origin_agent_id="a",
        origin_role="strategy-developer",
        target_assets=["BTC"],
        target_timeframes=["1h"],
    )
    hid = str(hyp["id"])
    mark_crucible_viable(hid, evidence_id="E1", by="test")
    # Seed 3 candidate strategies -> a "family of work" -> expanded.
    with get_db() as conn:
        for i in range(3):
            conn.execute(
                """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
                   stage, status, hypothesis_id, owner, params, metrics, verdict,
                   created_at, updated_at)
                   VALUES (?, ?, 'n', 'rsi', 'BTC', '1h', 'gauntlet', 'active', ?, 'brain',
                           '{}', '{}', '{}', datetime('now'), datetime('now'))""",
                (f"SX{i}", f"SX{i}", hid),
            )

    detail = get_hypothesis_detail_payload(hid)["hypothesis"]
    assert detail["crucible_status"] == "expanded"
    assert detail["protection_status"] == "protected"
    assert "contested_at" in detail
    assert "initial_viability_evidence_id" in detail

    summaries = list_hypotheses_summary()
    row = next(s for s in summaries if str(s["id"]) == hid)
    # Summary has no descendant-stage info, so it derives from strategy_count (>=3 -> expanded).
    assert row["crucible_status"] == "expanded"
    assert row["protection_status"] == "protected"


def test_detail_payload_includes_child_gauntlet_status(AXIOM_db):
    """1c/Phase-2: Forge rows show each candidate's real gauntlet (proof) status."""
    from axiom.api_domains.hypotheses import get_hypothesis_detail_payload
    from axiom.gauntlet.store import init_gauntlet_schema
    from axiom.hypotheses import create_hypothesis

    hyp = create_hypothesis(
        title="t", market_thesis="m", mechanism="x", why_now=None,
        lane="benchmarking", source_type="agent_original",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC"], target_timeframes=["1h"],
    )
    hid = str(hyp["id"])
    with get_db() as conn:
        conn.execute(
            """INSERT INTO strategies (id, display_id, name, type, symbol, timeframe,
               stage, status, hypothesis_id, owner, params, metrics, verdict,
               created_at, updated_at)
               VALUES ('SG1', 'SG1', 'n', 'rsi', 'BTC', '1h', 'gauntlet', 'active', ?,
                       'brain', '{}', '{}', '{}', datetime('now'), datetime('now'))""",
            (hid,),
        )
        init_gauntlet_schema(conn)
        conn.execute(
            """INSERT INTO gauntlet_workflows
               (id, strategy_id, definition_version, status, created_at, updated_at)
               VALUES ('gw1', 'SG1', 1, 'passed', datetime('now'), datetime('now'))""",
        )

    detail = get_hypothesis_detail_payload(hid)
    strat = next(s for s in detail["strategies"] if s["id"] == "SG1")
    assert strat["gauntlet_status"] == "passed"
