"""Robustness-suite audit — Phase 1 bug fixes (2026-06-14).

1. cost_stress gate extraction read a top-level `stressed_sharpe` that the test
   never stores (it's nested under `stressed.sharpe`) — the cost floor was a dead
   gate. Now reads the nested value.
2. walk-forward fold pass-rate counted near-empty folds in the denominator,
   dragging a consistently-positive strategy toward a false reject. Now folds with
   < wfa_min_fold_trades OOS trades are excluded from BOTH numerator and
   denominator (with a legacy fallback when no per-fold trade counts exist).
3. param-jitter pass-rate was inverted for non-positive baselines (the weakest
   strategies got the easiest "any run > 0" bar). Now a no-edge baseline must show
   a robustly-positive perturbed cloud (median > 0) to earn any credit.
"""

import numpy as np

from axiom.policy import _validation_row_to_verdict_payload
from axiom.routers.robustness import _jitter_pass_rate


# --- 1. cost_stress nested extraction -------------------------------------

def test_cost_stress_extraction_reads_nested_stressed_sharpe():
    metrics = {"verdict": "PASS", "degradation_pct": 12.0, "stressed": {"sharpe": 0.47}}
    payload = _validation_row_to_verdict_payload("cost_stress", metrics, {})
    assert payload["stressed_sharpe"] == 0.47, "must read the nested stressed.sharpe"


def test_cost_stress_extraction_still_honors_legacy_top_level():
    metrics = {"verdict": "PASS", "stressed_sharpe": 0.31}
    payload = _validation_row_to_verdict_payload("cost_stress", metrics, {})
    assert payload["stressed_sharpe"] == 0.31


# --- 2. walk-forward fold de-noising --------------------------------------

def _wf_metrics(folds):
    # folds: list of (oos_sharpe, oos_trades)
    return {
        "verdict": "PASS",
        "splits": [
            {"out_of_sample": {"sharpe": s, "total_trades": t}} for (s, t) in folds
        ],
    }


def test_wfa_excludes_empty_folds_from_pass_rate(AXIOM_db):
    # Positive in all 3 folds it traded; 2 folds had no trades (sat out flat
    # windows). Old behavior: 3/5 = 0.60. New: 3/3 = 1.0 (empty folds dropped).
    metrics = _wf_metrics([(1.2, 20), (0.8, 15), (0.5, 12), (0.0, 0), (0.0, 1)])
    payload = _validation_row_to_verdict_payload("walk_forward", metrics, {})
    assert payload["pass_rate"] == 1.0
    assert payload["folds"] == 3  # only the folds that actually traded


def test_wfa_falls_back_to_raw_rate_without_fold_trade_counts(AXIOM_db):
    # Legacy/fixture splits with no per-fold trade counts -> old sharpe-based rate.
    metrics = {
        "verdict": "PASS",
        "splits": [
            {"out_of_sample": {"sharpe": 1.0}},
            {"out_of_sample": {"sharpe": -0.5}},
            {"out_of_sample": {"sharpe": 0.3}},
            {"out_of_sample": {"sharpe": -0.1}},
        ],
    }
    payload = _validation_row_to_verdict_payload("walk_forward", metrics, {})
    assert payload["pass_rate"] == 0.5  # 2 of 4 positive (unchanged behavior)
    assert payload["folds"] == 4


# --- 3. param-jitter inverted floor ---------------------------------------

def test_jitter_positive_baseline_requires_edge_retention():
    # Baseline 2.0, allowed_degradation 0.5 -> floor 1.0. Half the runs clear it.
    sharpes = np.array([1.5, 1.2, 0.4, 0.1])
    assert _jitter_pass_rate(sharpes, original_sharpe=2.0, allowed_degradation=0.5) == 0.5


def test_jitter_nonpositive_baseline_no_free_pass_on_coinflip():
    # No-edge baseline whose perturbations are a coin flip around zero
    # (median <= 0) must score 0 — NOT the old lenient "% positive".
    sharpes = np.array([0.2, -0.3, 0.1, -0.4, -0.1])  # median -0.1
    assert _jitter_pass_rate(sharpes, original_sharpe=-0.05, allowed_degradation=0.5) == 0.0


def test_jitter_nonpositive_baseline_credits_robustly_positive_cloud():
    # If a no-edge baseline's perturbations are robustly positive (median > 0),
    # it still gets credit for the positive share.
    sharpes = np.array([0.4, 0.5, 0.6, -0.1])  # median 0.45 > 0, 3/4 positive
    assert _jitter_pass_rate(sharpes, original_sharpe=0.0, allowed_degradation=0.5) == 0.75
