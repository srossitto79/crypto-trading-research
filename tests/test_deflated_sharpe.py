"""Math regression for the Deflated Sharpe Ratio guard (gauntlet/deflated_sharpe.py)."""

from __future__ import annotations

import math

from axiom.gauntlet.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)


# --- PSR ---------------------------------------------------------------------

def test_psr_at_benchmark_is_half():
    # sr_hat == benchmark -> z = 0 -> CDF = 0.5
    assert probabilistic_sharpe_ratio(0.5, 0.5, 100, 0.0, 3.0) == 0.5


def test_psr_above_benchmark_exceeds_half():
    assert probabilistic_sharpe_ratio(0.5, 0.0, 100, 0.0, 3.0) > 0.5


def test_psr_below_benchmark_under_half():
    assert probabilistic_sharpe_ratio(0.0, 0.5, 100, 0.0, 3.0) < 0.5


def test_psr_negative_skew_lowers_confidence():
    # Negative skew inflates the denominator -> lower PSR (fatter left tail).
    hi = probabilistic_sharpe_ratio(0.4, 0.0, 200, 0.0, 3.0)
    lo = probabilistic_sharpe_ratio(0.4, 0.0, 200, -1.5, 3.0)
    assert lo < hi


# --- expected max sharpe -----------------------------------------------------

def test_expected_max_sharpe_grows_with_trials():
    v = 0.01
    assert expected_max_sharpe(v, 1000) > expected_max_sharpe(v, 10) > 0.0


def test_expected_max_sharpe_zero_for_single_trial():
    assert expected_max_sharpe(0.01, 1) == 0.0


# --- DSR ---------------------------------------------------------------------

_STRONG = [0.02] * 60 + [-0.005] * 40   # positive mean, modest dispersion
_WEAK = [0.01, -0.0095] * 60            # near-zero edge


def test_dsr_bounds_and_keys():
    out = deflated_sharpe_ratio(_STRONG, n_trials=10)
    assert out["dsr"] is not None
    assert 0.0 <= out["dsr"] <= 1.0
    assert out["n_obs"] == len(_STRONG)
    assert {"sr_hat", "sr0_benchmark", "skew", "kurtosis"} <= set(out)


def test_dsr_deflates_with_more_trials():
    few = deflated_sharpe_ratio(_STRONG, n_trials=2)["dsr"]
    many = deflated_sharpe_ratio(_STRONG, n_trials=5000)["dsr"]
    assert many <= few  # more trials -> higher selection benchmark -> lower DSR


def test_dsr_strong_beats_weak():
    strong = deflated_sharpe_ratio(_STRONG, n_trials=50)["dsr"]
    weak = deflated_sharpe_ratio(_WEAK, n_trials=50)["dsr"]
    assert strong > weak


def test_dsr_insufficient_returns():
    assert deflated_sharpe_ratio([0.01], n_trials=10)["dsr"] is None


def test_dsr_zero_variance():
    assert deflated_sharpe_ratio([0.01] * 50, n_trials=10)["dsr"] is None


def test_dsr_scale_invariant():
    # Returns in ratio vs percent units give the same DSR (mean/std cancels scale).
    ratio = deflated_sharpe_ratio(_STRONG, n_trials=20)["dsr"]
    pct = deflated_sharpe_ratio([r * 100.0 for r in _STRONG], n_trials=20)["dsr"]
    assert math.isclose(ratio, pct, abs_tol=1e-6)


# --- wiring ------------------------------------------------------------------

def test_dsr_gate_defaults_observe_first():
    # Gate ships OFF (observe-first); threshold present and sane.
    from axiom.policy import DEFAULT_PIPELINE_CONFIG

    rob = DEFAULT_PIPELINE_CONFIG["robustness_thresholds"]
    assert rob["deflated_sharpe_gate_enabled"] is False
    assert 0.0 < float(rob["min_deflated_sharpe"]) <= 1.0
    assert int(rob["deflated_sharpe_default_trials"]) >= 1


def test_compute_strategy_dsr_best_effort(AXIOM_db):
    # Unknown strategy must never raise — DSR is advisory.
    from axiom.gauntlet.deflated_sharpe import compute_strategy_dsr

    assert compute_strategy_dsr("does-not-exist") is None
