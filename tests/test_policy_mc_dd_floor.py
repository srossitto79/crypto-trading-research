"""H-2 regression: the Monte-Carlo 95th-pct drawdown safety floor must not
reject on an invented sentinel when the payload carries no measured drawdown.

Before the fix, ``mc_payload.get("max_dd_p95", ...999)`` turned a display-proxy
monte_carlo blob (no dd key) into a guaranteed rejection with the nonsense
message "DD 99900.0% exceeds 50% limit" — which exposed the entire live paper
roster (incl. the first-ever promotion S08808) to a hard reject on any
re-evaluation at the gauntlet->paper gate.
"""
from __future__ import annotations

from axiom.policy import _mc_dd_floor_reject


def test_absent_dd_key_does_not_reject():
    # Display-proxy shape: status/value/threshold/message only, no dd key.
    proxy = {"status": "pass", "value": 0.2, "threshold": 0.5, "message": "ok"}
    assert _mc_dd_floor_reject(proxy, 0.5) is None


def test_real_breach_still_rejects():
    payload = {"max_dd_p95": 0.62}
    msg = _mc_dd_floor_reject(payload, 0.5)
    assert msg is not None
    assert "62.0%" in msg
    assert "99900" not in msg


def test_genuine_zero_drawdown_is_enforced_not_skipped():
    # 0.0 is a real measurement: it is <= limit so it passes (no reject), but it
    # must be treated as present, not absent.
    payload = {"max_dd_p95": 0.0}
    assert _mc_dd_floor_reject(payload, 0.5) is None
    # And a 0.0 limit with a tiny positive dd rejects (proves 0.0 isn't "absent").
    assert _mc_dd_floor_reject({"max_dd_p95": 0.01}, 0.0) is not None


def test_within_limit_passes():
    assert _mc_dd_floor_reject({"max_dd_p95": 0.30}, 0.5) is None


def test_alias_keys_resolved():
    assert _mc_dd_floor_reject({"p95_dd": 0.7}, 0.5) is not None
    assert _mc_dd_floor_reject({"drawdown_95th": 0.7}, 0.5) is not None


def test_non_numeric_dd_is_treated_as_absent():
    assert _mc_dd_floor_reject({"max_dd_p95": "n/a"}, 0.5) is None


def test_non_dict_payload_is_safe():
    assert _mc_dd_floor_reject(None, 0.5) is None
