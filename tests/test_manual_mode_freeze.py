from __future__ import annotations

import json

from axiom.control_plane import ops as control_plane_ops
from axiom.db import claim_pending_agent_tasks, claim_pending_tasks, create_task_container, get_db
from axiom.system_mode_policy import (
    reconcile_manual_mode_backlog,
    thaw_manual_mode_backlog,
)


def _insert_brain_task(*, status: str = "pending", source: str = "system") -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (type, payload, status, priority, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "brain_invoke",
                json.dumps({"message": "Run cycle.", "source": "test"}),
                status,
                1,
                source,
            ),
        )
        return int(cursor.lastrowid)


def _set_agent_task_status(task_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE agent_tasks SET status = ? WHERE id = ?", (status, task_id))


def test_manual_mode_freezes_autonomous_backlog_and_auto_mode_thaws_it(AXIOM_db):
    with get_db() as conn:
        system_agent_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="Autonomous research",
            description="",
            input_data={"origin_mode": "autonomous"},
            source="system",
        )
        user_agent_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="User research",
            description="",
            input_data={"origin_mode": "operator_manual_entry"},
            source="user",
        )
        running_agent_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="Running research",
            description="",
            input_data={"origin_mode": "autonomous"},
            strategy_id="S-MANUAL-RUNNING",
            source="system",
        )
    _set_agent_task_status(running_agent_id, "running")

    system_brain_id = _insert_brain_task(status="pending", source="system")
    user_brain_id = _insert_brain_task(status="pending", source="user")
    running_brain_id = _insert_brain_task(status="running", source="system")

    payload = control_plane_ops.update_system_mode("manual")

    with get_db() as conn:
        agent_rows = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM agent_tasks WHERE id IN (?, ?, ?)",
                (system_agent_id, user_agent_id, running_agent_id),
            ).fetchall()
        }
        brain_rows = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM tasks WHERE id IN (?, ?, ?)",
                (system_brain_id, user_brain_id, running_brain_id),
            ).fetchall()
        }

    assert agent_rows == {
        system_agent_id: "paused_manual",
        user_agent_id: "pending",
        running_agent_id: "running",
    }
    assert brain_rows == {
        system_brain_id: "paused_manual",
        user_brain_id: "pending",
        running_brain_id: "running",
    }
    assert payload["paused_manual_counts"] == {
        "agent_tasks": 1,
        "brain_tasks": 1,
        "total": 2,
    }
    assert control_plane_ops.get_system_mode_status()["paused_manual_counts"]["total"] == 2

    resumed = control_plane_ops.update_system_mode("auto")

    with get_db() as conn:
        agent_rows = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM agent_tasks WHERE id IN (?, ?, ?)",
                (system_agent_id, user_agent_id, running_agent_id),
            ).fetchall()
        }
        brain_rows = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM tasks WHERE id IN (?, ?, ?)",
                (system_brain_id, user_brain_id, running_brain_id),
            ).fetchall()
        }

    assert agent_rows == {
        system_agent_id: "pending",
        user_agent_id: "pending",
        running_agent_id: "running",
    }
    assert brain_rows == {
        system_brain_id: "pending",
        user_brain_id: "pending",
        running_brain_id: "running",
    }
    assert resumed["paused_manual_counts"] == {
        "agent_tasks": 0,
        "brain_tasks": 0,
        "total": 0,
    }


def test_thaw_manual_mode_backlog_skips_duplicate_agent_tasks(AXIOM_db):
    with get_db() as conn:
        first = conn.execute(
            """
            INSERT INTO agent_tasks (agent_id, type, title, strategy_id, status, source, priority)
            VALUES (?, ?, ?, ?, 'paused_manual', 'system', ?)
            """,
            ("strategy-developer", "code_strategy", "Older duplicate", "S-DUP-001", 1),
        ).lastrowid
        second = conn.execute(
            """
            INSERT INTO agent_tasks (agent_id, type, title, strategy_id, status, source, priority)
            VALUES (?, ?, ?, ?, 'paused_manual', 'system', ?)
            """,
            ("strategy-developer", "code_strategy", "Newer duplicate", "S-DUP-001", 5),
        ).lastrowid

    result = thaw_manual_mode_backlog()

    with get_db() as conn:
        rows = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM agent_tasks WHERE id IN (?, ?)",
                (first, second),
            ).fetchall()
        }

    assert result["agent_tasks"] == 1
    assert result["skipped_duplicate_agent_tasks"] == 1
    assert rows == {
        int(first): "cancelled",
        int(second): "pending",
    }


def test_reconcile_thaws_stale_paused_manual_when_mode_is_auto(AXIOM_db):
    """Regression for app-restart silent killer: paused_manual rows linger from a
    prior manual session, system has since flipped to auto. Reconcile must thaw
    them so the queue actually drains, not just return a count.
    """
    # System mode is auto: the user already flipped out of manual.
    control_plane_ops.update_system_mode("auto")
    # Seed a pair of paused_manual rows directly — emulate the post-restart
    # state where the freeze ran in a prior process and the mode then flipped
    # to auto without the in-process transition hook firing.
    with get_db() as conn:
        agent_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="Stranded autonomous research",
            description="",
            input_data={"origin_mode": "autonomous"},
            source="system",
        )
        brain_cursor = conn.execute(
            """
            INSERT INTO tasks (type, payload, status, priority, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "brain_invoke",
                json.dumps({"message": "stranded brain task"}),
                "paused_manual",
                1,
                "system",
            ),
        )
        brain_id = int(brain_cursor.lastrowid)
        conn.execute(
            "UPDATE agent_tasks SET status = 'paused_manual' WHERE id = ?",
            (agent_id,),
        )

    counts = reconcile_manual_mode_backlog()
    assert counts["total"] == 0, "stuck rows should be thawed"

    with get_db() as conn:
        assert conn.execute(
            "SELECT status FROM agent_tasks WHERE id = ?", (agent_id,)
        ).fetchone()["status"] == "pending"
        assert conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (brain_id,)
        ).fetchone()["status"] == "pending"


def test_reconcile_is_noop_when_auto_and_no_stuck_rows(AXIOM_db):
    """No paused_manual rows → reconcile must not invoke thaw or perturb queue."""
    control_plane_ops.update_system_mode("auto")
    counts = reconcile_manual_mode_backlog()
    assert counts == {"agent_tasks": 0, "brain_tasks": 0, "total": 0}


def test_claim_pending_agent_tasks_in_manual_mode_only_claims_user_work(AXIOM_db):
    control_plane_ops.update_system_mode("manual")

    with get_db() as conn:
        system_task_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="Autonomous research",
            description="",
            input_data={"origin_mode": "autonomous"},
            source="system",
        )
        user_task_id, _ = create_task_container(
            conn=conn,
            agent_id="strategy-developer",
            task_type="research",
            title="User research",
            description="",
            input_data={"origin_mode": "operator_manual_entry"},
            source="user",
        )

    claimed = claim_pending_agent_tasks("strategy-developer", limit=5)

    assert [int(task["id"]) for task in claimed] == [user_task_id]
    with get_db() as conn:
        statuses = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM agent_tasks WHERE id IN (?, ?)",
                (system_task_id, user_task_id),
            ).fetchall()
        }
    assert statuses == {
        system_task_id: "paused_manual",
        user_task_id: "running",
    }


def test_claim_pending_brain_tasks_in_manual_mode_only_claims_user_work(AXIOM_db):
    control_plane_ops.update_system_mode("manual")

    system_task_id = _insert_brain_task(status="pending", source="system")
    user_task_id = _insert_brain_task(status="pending", source="user")

    claimed = claim_pending_tasks("brain_invoke", limit=5, priority=True)

    assert [int(task["id"]) for task in claimed] == [user_task_id]
    with get_db() as conn:
        statuses = {
            int(row["id"]): str(row["status"])
            for row in conn.execute(
                "SELECT id, status FROM tasks WHERE id IN (?, ?)",
                (system_task_id, user_task_id),
            ).fetchall()
        }
    assert statuses == {
        system_task_id: "pending",
        user_task_id: "running",
    }
