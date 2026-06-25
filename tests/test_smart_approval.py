"""Phase 5 / P5-T03 — smart approval classifier.

Tests the ``Axiom.control_plane.smart_approval`` module:

* Hard rules override the model:
    - ``approval_type`` matching live/real-money/withdraw → forced ``escalate``.
    - Payload >64 KB → forced ``hold``.
* Malformed model output → ``hold`` (never auto_approve on error).
* Unreachable auxiliary model → ``hold`` (fail-closed).
* Successful auto_approve path persists classifier columns and (for
  ``apply_smart_decision`` in smart mode) actually approves with actor
  ``system:smart_approval`` and ``auto_approved=1``.

The auxiliary LLM is monkey-patched at ``smart_approval._call_aux_llm`` so no
network is hit and the test runs deterministically.
"""
from __future__ import annotations

import json


from axiom.control_plane import smart_approval as sa
from axiom.db import create_approval, get_approval, init_db


def _aux_returns(monkeypatch, payload: dict | str | Exception) -> None:
    """Force ``_call_aux_llm`` to return ``payload`` (or raise if exception)."""
    def _fake(prompt: str, routing) -> str:
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload) if isinstance(payload, dict) else payload
    monkeypatch.setattr(sa, "_call_aux_llm", _fake)


# --- Hard rules -----------------------------------------------------------

def test_force_escalate_on_live_trade(AXIOM_db, monkeypatch) -> None:
    """``approval_type='live_trade_arm'`` always escalates, even if the
    classifier says auto_approve."""
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "auto_approve",
                               "reasoning": "looks fine", "confidence": 0.99})

    approval_id = create_approval(
        approval_type="live_trade_arm",
        target_type="strategy",
        target_id="S0001",
        actor="brain",
        payload={"symbol": "BTC/USD"},
    )
    result = sa.classify_approval({
        "id": approval_id,
        "approval_type": "live_trade_arm",
        "payload": {"symbol": "BTC/USD"},
    })
    assert result["recommendation"] == "escalate"
    assert "high-stakes" in result["model"].lower() or "hard-rule" in result["model"].lower()


def test_force_escalate_on_real_money(AXIOM_db, monkeypatch) -> None:
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "auto_approve",
                               "reasoning": "x", "confidence": 1.0})
    result = sa.classify_approval({
        "id": None,
        "approval_type": "real_money_transfer",
        "payload": {"amount": 100},
    })
    assert result["recommendation"] == "escalate"


def test_force_hold_on_oversized_payload(AXIOM_db, monkeypatch) -> None:
    """Payload >64 KB always returns hold, regardless of model output."""
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "auto_approve",
                               "reasoning": "x", "confidence": 1.0})
    big_payload = {"blob": "x" * (sa.PAYLOAD_BYTE_CEILING + 1024)}
    result = sa.classify_approval({
        "id": None,
        "approval_type": "param_optimization",
        "payload": big_payload,
    })
    assert result["recommendation"] == "hold"
    assert "payload" in result["reasoning"].lower()


# --- Failure modes default to hold ----------------------------------------

def test_aux_unreachable_defaults_to_hold(AXIOM_db, monkeypatch) -> None:
    init_db()
    _aux_returns(monkeypatch, RuntimeError("network down"))
    result = sa.classify_approval({
        "id": None,
        "approval_type": "param_optimization",
        "payload": {"k": 1},
    })
    assert result["recommendation"] == "hold"
    assert result["confidence"] == 0.0


def test_malformed_response_defaults_to_hold(AXIOM_db, monkeypatch) -> None:
    init_db()
    _aux_returns(monkeypatch, "not json at all just prose")
    result = sa.classify_approval({
        "id": None,
        "approval_type": "param_optimization",
        "payload": {"k": 1},
    })
    assert result["recommendation"] == "hold"


def test_invalid_recommendation_value_defaults_to_hold(AXIOM_db, monkeypatch) -> None:
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "definitely_yes",
                               "reasoning": "go", "confidence": 0.9})
    result = sa.classify_approval({
        "id": None,
        "approval_type": "param_optimization",
        "payload": {"k": 1},
    })
    assert result["recommendation"] == "hold"


# --- Happy path: persistence + apply_smart_decision -----------------------

def test_classify_persists_columns_to_approvals_row(AXIOM_db, monkeypatch) -> None:
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "auto_approve",
                               "reasoning": "routine paper deploy",
                               "confidence": 0.85})

    approval_id = create_approval(
        approval_type="param_optimization",
        target_type="strategy",
        target_id="S0042",
        actor="brain",
        payload={"params": {"adx_max": 25}},
    )
    sa.classify_approval({
        "id": approval_id,
        "approval_type": "param_optimization",
        "payload": {"params": {"adx_max": 25}},
    })

    row = get_approval(approval_id)
    assert row is not None
    assert row["classifier_recommendation"] == "auto_approve"
    assert row["classifier_reasoning"] == "routine paper deploy"
    assert row["classifier_at"]


def test_apply_smart_decision_auto_approves_in_smart_mode(AXIOM_db, monkeypatch) -> None:
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "auto_approve",
                               "reasoning": "safe routine",
                               "confidence": 0.95})

    # Disable the auto-classify-on-create hook so apply_smart_decision is the
    # one driving the state transition we're verifying.
    monkeypatch.setattr(
        "axiom.control_plane.approval_modes.get_mode",
        lambda *_args, **_kw: "manual",
    )

    approval_id = create_approval(
        approval_type="param_optimization",
        target_type="strategy",
        target_id="S0099",
        actor="brain",
        payload={"params": {"rsi_period": 14}},
    )

    result = sa.apply_smart_decision(approval_id, "smart")
    assert result["recommendation"] == "auto_approve"
    assert result["applied"] is True

    row = get_approval(approval_id)
    assert row is not None
    assert row["auto_approved"] == 1
    # Approved status — column is populated by post_approve_approval.
    assert str(row.get("status") or "").lower() in ("approved", "completed")


def test_apply_smart_decision_no_op_in_manual_mode(AXIOM_db, monkeypatch) -> None:
    """In manual mode, classifier still runs but auto-approve is NOT applied."""
    init_db()
    _aux_returns(monkeypatch, {"recommendation": "auto_approve",
                               "reasoning": "looks safe",
                               "confidence": 0.9})

    monkeypatch.setattr(
        "axiom.control_plane.approval_modes.get_mode",
        lambda *_args, **_kw: "manual",
    )

    approval_id = create_approval(
        approval_type="param_optimization",
        target_type="strategy",
        target_id="S0100",
        actor="brain",
        payload={"params": {}},
    )

    result = sa.apply_smart_decision(approval_id, "manual")
    assert result["applied"] is False

    row = get_approval(approval_id)
    assert row is not None
    assert (row.get("auto_approved") or 0) == 0
    # Status still pending_approval — operator must approve manually.
    assert str(row.get("status") or "").lower().startswith("pending")


def test_apply_smart_decision_returns_hold_for_missing_row(AXIOM_db) -> None:
    init_db()
    result = sa.apply_smart_decision(99999999, "smart")
    assert result["recommendation"] == "hold"
    assert result["applied"] is False
