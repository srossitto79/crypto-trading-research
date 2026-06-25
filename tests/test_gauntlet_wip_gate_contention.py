"""run_quick_screen_gate must treat a gauntlet-WIP-cap wait as gate_contention.

A strategy blocked at quick_screen->gauntlet by the 50-slot WIP cap is admissible
and merely WAITING for a free slot -- it must NOT be drained to failed_gate (which
archives it). Marking the block reason_code='gate_contention' makes the engine
exempt it from the attempt budget (retried forever on the slow cadence), the same
proven path the paper gate uses for capital-slot waits. Other transient blocks
(canonical_backtest_required, ...) must stay on the bounded budget, and the hard
overfitting verdict must stay terminal.
"""
from __future__ import annotations

import json


def _patch_gate(monkeypatch, transition):
    from axiom.gauntlet import tasks
    import axiom.brain as brain

    monkeypatch.setattr(tasks, "_workflow_settings", lambda wf: {})
    monkeypatch.setattr(tasks, "_detail_for_workflow", lambda wid: {})
    monkeypatch.setattr(tasks, "_step_output", lambda detail, key: {"metrics": {}})
    monkeypatch.setattr(tasks, "_quick_screen_failures", lambda metrics, cfg: [])
    monkeypatch.setattr(brain, "transition_stage", lambda **kw: transition)
    return tasks


def test_wip_cap_block_is_gate_contention(monkeypatch):
    tasks = _patch_gate(monkeypatch, {
        "to": "quick_screen", "reason_code": "wip_cap_exceeded",
        "blocked_reason": "WIP cap reached for 'gauntlet': 50/50",
    })
    out = tasks.run_quick_screen_gate({"id": "wf1", "strategy_id": "S1"}, {})
    assert out["status"] == "blocked_runtime"
    assert out["retryable"] is True
    assert out["reason_code"] == "gate_contention"

    # Contract: the engine reads this as gate_contention and exempts it from drain.
    from axiom.gauntlet.engine import _step_block_reason_code, _NO_DRAIN_REASON_CODES
    assert _step_block_reason_code(json.dumps(out)) == "gate_contention"
    assert "gate_contention" in _NO_DRAIN_REASON_CODES


def test_other_transient_block_stays_bounded(monkeypatch):
    tasks = _patch_gate(monkeypatch, {"to": "quick_screen", "reason_code": "canonical_backtest_required"})
    out = tasks.run_quick_screen_gate({"id": "wf1", "strategy_id": "S1"}, {})
    assert out["status"] == "blocked_runtime"
    assert out.get("reason_code") != "gate_contention"  # not exempt -> bounded budget
    from axiom.gauntlet.engine import _step_block_reason_code
    assert _step_block_reason_code(json.dumps(out)) == "canonical_backtest_required"


def test_overfitting_verdict_stays_terminal(monkeypatch):
    tasks = _patch_gate(monkeypatch, {
        "to": "quick_screen", "reason_code": "overfitting_guardrails",
        "blocked_reason": "Gate5: Trades 0 < 30 (reject)",
    })
    out = tasks.run_quick_screen_gate({"id": "wf1", "strategy_id": "S1"}, {})
    assert out["status"] == "failed_gate"  # deterministic quality reject -> drains/archives


def test_successful_transition_passes(monkeypatch):
    tasks = _patch_gate(monkeypatch, {"to": "gauntlet"})
    out = tasks.run_quick_screen_gate({"id": "wf1", "strategy_id": "S1"}, {})
    assert out["status"] == "passed"
