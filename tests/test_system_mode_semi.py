from __future__ import annotations

import json

from axiom.control_plane import ops as control_plane_ops
from axiom.db import create_task_container, get_db
from axiom.system_mode_policy import (
    autonomous_hardening_allowed,
    autonomous_hypothesis_generation_allowed,
)


def test_semi_mode_policy_allows_hardening_but_blocks_generation(AXIOM_db):
    assert autonomous_hardening_allowed("manual") is False
    assert autonomous_hypothesis_generation_allowed("manual") is False

    assert autonomous_hardening_allowed("semi_auto") is True
    assert autonomous_hypothesis_generation_allowed("semi_auto") is False

    assert autonomous_hardening_allowed("auto") is True
    assert autonomous_hypothesis_generation_allowed("auto") is True


def test_semi_mode_allows_system_hardening_tasks_to_be_claimed(AXIOM_db):
    from axiom.db import claim_pending_agent_tasks

    control_plane_ops.update_system_mode("semi_auto")

    with get_db() as conn:
        task_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="backtest",
            title="Harden existing hypothesis",
            description="Run a backtest against an existing hypothesis strategy.",
            input_data={
                "origin_mode": "autonomous_hardening",
                "hypothesis_id": "H00001",
            },
            source="system",
        )

    claimed = claim_pending_agent_tasks("strategy-developer", limit=5)

    assert [int(task["id"]) for task in claimed] == [task_id]


def test_semi_mode_blocks_agent_hypothesis_creation_tool(AXIOM_db):
    from axiom.agents.tools_research import _tool_create_hypothesis

    control_plane_ops.update_system_mode("semi_auto")

    result = json.loads(
        _tool_create_hypothesis({
            "title": "Semi-auto should not create hypotheses",
            "market_thesis": "New external ideation must stay paused in semi-auto.",
            "mechanism": "This is only a policy contract test.",
            "lane": "exploration",
            "source_type": "agent_original",
            "target_assets": ["BTC"],
            "target_timeframes": ["1h"],
        })
    )

    assert result["ok"] is False
    assert result["error_code"] == "generation_paused"
    assert result["system_mode"] == "semi_auto"

# --- B-28 (2026-06-09 audit): pause/halt toggles must not silently exit -------
# manual mode. set_system_paused/set_generation_paused used to recompute the
# mode via _flags_to_mode, which can only produce semi_auto/auto — so any of
# stop/start system, pause/resume generation (or emergency-stop / halt-reset,
# which share set_system_paused) silently flipped a manually-frozen system out
# of manual and the mode transition thawed the entire frozen backlog.


def _agent_task_status(task_id: int) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM agent_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return str(row["status"])


def test_stop_start_system_preserves_manual_mode(AXIOM_db):
    from axiom.system_pause import get_system_mode, is_system_paused

    control_plane_ops.update_system_mode("manual")

    control_plane_ops.stop_system()
    assert get_system_mode() == "manual"
    assert is_system_paused() is True

    control_plane_ops.start_system()
    assert get_system_mode() == "manual"
    assert is_system_paused() is False


def test_stop_start_system_preserves_semi_auto_mode(AXIOM_db):
    from axiom.system_pause import get_system_mode

    control_plane_ops.update_system_mode("semi_auto")
    control_plane_ops.stop_system()
    assert get_system_mode() == "semi_auto"
    control_plane_ops.start_system()
    assert get_system_mode() == "semi_auto"


def test_resume_generation_in_manual_mode_stays_manual_and_does_not_thaw(AXIOM_db):
    from axiom.system_pause import get_system_mode, is_generation_paused

    with get_db() as conn:
        frozen_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="Autonomous research",
            description="",
            input_data={"origin_mode": "autonomous"},
            source="system",
        )
    control_plane_ops.update_system_mode("manual")
    assert _agent_task_status(frozen_id) == "paused_manual"

    result = control_plane_ops.resume_strategy_generation()

    # Conservative refusal: manual is an explicit operator freeze.
    assert get_system_mode() == "manual"
    assert is_generation_paused() is True
    assert result["generation_paused"] is True
    assert _agent_task_status(frozen_id) == "paused_manual", "frozen backlog was thawed"

    # Pausing generation in manual is a no-op for the mode too.
    control_plane_ops.pause_strategy_generation()
    assert get_system_mode() == "manual"
    assert _agent_task_status(frozen_id) == "paused_manual"

    # Leaving manual must remain possible — but only via the explicit mode API.
    control_plane_ops.update_system_mode("auto")
    assert get_system_mode() == "auto"
    assert _agent_task_status(frozen_id) == "pending"


def test_generation_toggle_still_moves_between_semi_auto_and_auto(AXIOM_db):
    from axiom.system_pause import get_system_mode

    control_plane_ops.update_system_mode("semi_auto")
    control_plane_ops.resume_strategy_generation()
    assert get_system_mode() == "auto"

    control_plane_ops.pause_strategy_generation()
    assert get_system_mode() == "semi_auto"


def test_fresh_install_default_manual_survives_stop_start(AXIOM_db):
    """A fresh install (no mode ever persisted) reports manual; the first
    stop/start must not silently promote it to semi_auto/auto."""
    from axiom.system_pause import get_system_mode

    assert get_system_mode() == "manual"
    control_plane_ops.stop_system()
    assert get_system_mode() == "manual"
    control_plane_ops.start_system()
    assert get_system_mode() == "manual"
