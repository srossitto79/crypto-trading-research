"""Guard against gauntlet test-key / result-type alias drift.

The robustness producers (gauntlet steps), the status reader, and the policy
promotion gate must agree on how a result_type / step_key / required_test name
canonicalises — otherwise a required test silently never matches a passed step
and the gauntlet->paper gate stalls forever (the T07-F2 class of bug, fixed by
adding parameter_stability/regime_performance aliases). These are pure-logic
assertions so a future rename that breaks the contract fails in CI immediately.
"""
from __future__ import annotations

from axiom.gauntlet.models import (
    ROBUSTNESS_STEP_KEYS,
    STEP_KEY_ALIASES,
    normalize_step_key,
)
from axiom.gauntlet.settings import normalize_required_tests
from axiom.gauntlet.status import _RESULT_TYPE_TO_STEP, _STEP_TO_RESULT_TYPE


def test_result_type_to_step_targets_are_real_robustness_keys():
    """Every result_type the status reader recognises maps to a canonical step."""
    for result_type, step_key in _RESULT_TYPE_TO_STEP.items():
        assert step_key in ROBUSTNESS_STEP_KEYS, (
            f"result_type {result_type!r} maps to {step_key!r} which is not a "
            f"ROBUSTNESS_STEP_KEY {ROBUSTNESS_STEP_KEYS}"
        )


def test_every_robustness_step_round_trips_to_a_known_result_type():
    """Each robustness step has a result_type the status reader can canonicalise back."""
    for step_key in ROBUSTNESS_STEP_KEYS:
        result_type = _STEP_TO_RESULT_TYPE.get(step_key)
        assert result_type is not None, f"step {step_key!r} has no result_type mapping"
        assert _RESULT_TYPE_TO_STEP.get(result_type) == step_key, (
            f"round-trip mismatch: step {step_key!r} -> result_type {result_type!r} "
            f"-> {_RESULT_TYPE_TO_STEP.get(result_type)!r}"
        )


def test_step_key_aliases_resolve_to_real_robustness_keys():
    """Every alias canonicalises to an actual robustness step key (no dead aliases)."""
    for alias, canonical in STEP_KEY_ALIASES.items():
        assert normalize_step_key(alias) == canonical
        assert canonical in ROBUSTNESS_STEP_KEYS, (
            f"alias {alias!r} -> {canonical!r} which is not a ROBUSTNESS_STEP_KEY"
        )


def test_operator_required_test_names_canonicalise_to_passable_steps():
    """Operator-facing required_tests names must resolve to keys the gate can pass.

    Regression guard for T07-F2: a required_tests entry under an operator alias
    (parameter_stability / regime_performance) used to never match a passed step,
    permanently stalling gauntlet->paper.
    """
    operator_names = [
        "walk_forward",
        "monte_carlo",
        "parameter_stability",  # operator alias for parameter_jitter
        "regime_performance",   # operator alias for regime_split
        "cost_stress",
    ]
    canonical = normalize_required_tests(operator_names)
    for key in canonical:
        assert key in ROBUSTNESS_STEP_KEYS, (
            f"required test {key!r} does not canonicalise to a passable robustness step"
        )
    # The two operator aliases specifically must land on their canonical steps.
    assert "parameter_jitter" in canonical
    assert "regime_split" in canonical
