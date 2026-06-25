"""Gauntlet wedge-audit hardening (2026-06-14).

Fixes for three ways the pipeline could silently stop:
1. A background scheduler job (e.g. the gauntlet step-loop) whose in-memory flag
   leaked + DB lock stuck was SKIPPED by stale-lock recovery forever — now an
   absolute-max backstop force-recovers it when no live task owns it.
2. tick_active_gauntlet_workflows had no wall-clock budget; a slow late step could
   overrun the job timeout and orphan a worker. Now it stops claiming past a budget.
3. A gauntlet async result stuck 'running' (zombie optimization) was polled forever
   (the step heartbeat hides it from stale-step recovery). Now the step abandons it
   past an absolute age and re-submits.
"""

import json
from datetime import datetime, timedelta, timezone

from axiom.db import get_db


# --- Fix 1: background-job lock absolute backstop --------------------------

def test_stale_lock_recovery_backstops_leaked_background_job(AXIOM_db, monkeypatch):
    import axiom.scheduler as sched

    # Simulate the gauntlet step-loop's lock held since well past the absolute max,
    # with its job_id leaked in the in-memory background set but NO live task.
    old = (datetime.now(timezone.utc) - timedelta(seconds=sched._ABSOLUTE_MAX_RUNNING_SECONDS + 600)).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO scheduler_jobs (id, name, schedule_type, schedule_expr, command, enabled, running_since) "
            "VALUES ('Axiom-gauntlet-step-loop', 'gauntlet step loop', 'interval', '120', 'gauntlet_step_loop', 1, ?)",
            (old,),
        )
        conn.commit()
    sched._SCHEDULER_BACKGROUND_JOB_IDS.add("Axiom-gauntlet-step-loop")
    monkeypatch.setattr(sched, "_job_has_live_background_task", lambda _jid: False)

    recovered = sched.recover_stale_scheduler_job_locks()

    assert recovered >= 1
    with get_db() as conn:
        rs = conn.execute("SELECT running_since FROM scheduler_jobs WHERE id='Axiom-gauntlet-step-loop'").fetchone()["running_since"]
    assert rs is None  # lock cleared -> job can run again
    assert "Axiom-gauntlet-step-loop" not in sched._SCHEDULER_BACKGROUND_JOB_IDS


def test_stale_lock_recovery_skips_live_background_job(AXIOM_db, monkeypatch):
    import axiom.scheduler as sched

    old = (datetime.now(timezone.utc) - timedelta(seconds=sched._ABSOLUTE_MAX_RUNNING_SECONDS + 600)).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO scheduler_jobs (id, name, schedule_type, schedule_expr, command, enabled, running_since) "
            "VALUES ('Axiom-gauntlet-step-loop', 'gauntlet step loop', 'interval', '120', 'gauntlet_step_loop', 1, ?)",
            (old,),
        )
        conn.commit()
    sched._SCHEDULER_BACKGROUND_JOB_IDS.add("Axiom-gauntlet-step-loop")
    # A live task IS running it -> must NOT be force-recovered (would double-run).
    monkeypatch.setattr(sched, "_job_has_live_background_task", lambda _jid: True)
    try:
        sched.recover_stale_scheduler_job_locks()
        with get_db() as conn:
            rs = conn.execute("SELECT running_since FROM scheduler_jobs WHERE id='Axiom-gauntlet-step-loop'").fetchone()["running_since"]
        assert rs is not None  # left alone — a live task owns it
    finally:
        sched._SCHEDULER_BACKGROUND_JOB_IDS.discard("Axiom-gauntlet-step-loop")


# --- Fix 3: tick wall-clock budget ----------------------------------------

def test_tick_respects_wall_clock_deadline(AXIOM_db, monkeypatch):
    import axiom.gauntlet.engine as engine

    # Pretend there are 5 active workflows; each "step" sleeps a touch.
    monkeypatch.setattr(engine, "backfill_missing_quick_screen_workflows", lambda **k: 0)
    monkeypatch.setattr(engine, "requeue_retryable_blocked_steps", lambda **k: 0)
    monkeypatch.setattr(engine, "drain_exhausted_blocked_steps", lambda **k: 0)
    monkeypatch.setattr(engine, "demote_failed_gate_strategies", lambda **k: 0)
    monkeypatch.setattr(engine, "list_active_workflow_ids", lambda **k: [f"wf{i}" for i in range(5)])

    import time as _t
    ran = []

    def _fake_resume(wf_id, max_steps=1, runner=None, deadline_monotonic=None, **_kw):
        ran.append(wf_id)
        _t.sleep(0.05)
        return {"steps_run": 1}

    monkeypatch.setattr(engine, "resume_workflow", _fake_resume)

    summary = engine.tick_active_gauntlet_workflows(max_workflows=5, deadline_seconds=0.06)

    # Budget is tiny: it should stop early, not run all 5.
    assert summary["deadline_hit"] is True
    assert summary["skipped_for_deadline"] >= 1
    assert len(ran) < 5


# --- Fix 2: abandon zombie async optimization result ----------------------

def test_validation_optimization_resubmits_stale_running_result(AXIOM_db, monkeypatch):
    from axiom.gauntlet.tasks import run_validation_optimization

    sid = "zombie-opt"
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, stage_changed_at, created_at, updated_at) "
            "VALUES (?, ?, 'rsi_momentum', 'ETH', '1h', '{\"rsi_period\":14}', '{}', 'gauntlet', 'brain', 'gauntlet', ?, ?, ?)",
            (sid, sid, now, now, now),
        )
        # Optimization result stuck 'running', created 3h ago (> 60m cap).
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        conn.execute(
            "INSERT INTO backtest_results (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at) "
            "VALUES ('OPT-ZOMBIE', ?, 'optimization', 'ETH', '1h', '{}', ?, ?)",
            (sid, json.dumps({"status": "running"}), old),
        )
        conn.commit()

    resubmits = {"n": 0}

    def _fake_submit(body):
        resubmits["n"] += 1
        return {"result_id": "OPT-FRESH", "status": "succeeded", "best_params": {"rsi_period": 21}}

    monkeypatch.setattr("axiom.gauntlet.tasks._submit_optimization", _fake_submit)

    step = {"output_json": json.dumps({"result_id": "OPT-ZOMBIE"})}
    outcome = run_validation_optimization({"id": "wf-z", "strategy_id": sid}, step)

    assert resubmits["n"] == 1, "stale-running result must be abandoned and re-submitted"
    assert outcome["result_id"] == "OPT-FRESH"


def test_validation_optimization_keeps_polling_fresh_running_result(AXIOM_db, monkeypatch):
    from axiom.gauntlet.tasks import run_validation_optimization

    sid = "fresh-opt"
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, stage_changed_at, created_at, updated_at) "
            "VALUES (?, ?, 'rsi_momentum', 'ETH', '1h', '{}', '{}', 'gauntlet', 'brain', 'gauntlet', ?, ?, ?)",
            (sid, sid, now, now, now),
        )
        conn.execute(
            "INSERT INTO backtest_results (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at) "
            "VALUES ('OPT-FRESH-RUN', ?, 'optimization', 'ETH', '1h', '{}', ?, ?)",
            (sid, json.dumps({"status": "running"}), now),  # just started
        )
        conn.commit()

    called = {"n": 0}
    monkeypatch.setattr("axiom.gauntlet.tasks._submit_optimization", lambda body: called.__setitem__("n", called["n"] + 1))

    step = {"output_json": json.dumps({"result_id": "OPT-FRESH-RUN"})}
    outcome = run_validation_optimization({"id": "wf-f", "strategy_id": sid}, step)

    assert outcome["status"] == "running"
    assert called["n"] == 0  # still polling, did not re-submit
