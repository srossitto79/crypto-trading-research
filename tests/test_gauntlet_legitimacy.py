from __future__ import annotations

from axiom.gauntlet.legitimacy import validate_robustness_payload


def test_regime_split_with_one_regime_is_not_legitimate():
    verdict = validate_robustness_payload(
        "regime_split",
        {"verdict": "PASS", "n_regimes": 1, "profitable_regime_share": 1.0},
    )

    assert verdict["ok"] is False
    assert "at least 2 regimes" in verdict["reason"]


def test_walk_forward_without_folds_is_not_legitimate():
    verdict = validate_robustness_payload("walk_forward", {"verdict": "PASS", "splits": []})

    assert verdict["ok"] is False
    assert "fold" in verdict["reason"].lower()


def test_parameter_jitter_with_iterations_and_pass_rate_is_legitimate():
    verdict = validate_robustness_payload(
        "parameter_jitter",
        {"verdict": "PASS", "n_iterations": 50, "pass_rate": 0.76},
    )

    assert verdict["ok"] is True


def test_monte_carlo_with_one_trade_is_not_legitimate():
    verdict = validate_robustness_payload(
        "monte_carlo",
        {"verdict": "PASS", "n_simulations": 1000, "n_trades": 1, "min_trades": 10},
    )

    assert verdict["ok"] is False
    assert "baseline trades" in verdict["reason"]
