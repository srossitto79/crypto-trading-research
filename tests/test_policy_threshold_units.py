"""Pipeline threshold unit normalization tests."""

from axiom.db import kv_get, kv_set
from axiom.policy import _validation_row_to_verdict_payload, load_pipeline_config, save_pipeline_config


def test_save_pipeline_config_accepts_percent_points(AXIOM_db):
    save_pipeline_config(
        {
            "paper_gate": {"max_drawdown_pct": 40},
            "retirement": {"max_drawdown_pct": 25},
            "decay": {"degradation_threshold": 30},
        }
    )

    cfg = load_pipeline_config()
    assert abs(float(cfg["paper_gate"]["max_drawdown_pct"]) - 0.40) < 1e-9
    assert abs(float(cfg["retirement"]["max_drawdown_pct"]) - 0.25) < 1e-9
    assert abs(float(cfg["decay"]["degradation_threshold"]) - 0.30) < 1e-9

    stored = kv_get("axiom:pipeline_thresholds", {})
    assert abs(float(stored["paper_gate"]["max_drawdown_pct"]) - 0.40) < 1e-9
    assert abs(float(stored["retirement"]["max_drawdown_pct"]) - 0.25) < 1e-9
    assert abs(float(stored["decay"]["degradation_threshold"]) - 0.30) < 1e-9


def test_load_pipeline_config_heals_legacy_percent_point_values(AXIOM_db):
    kv_set(
        "axiom:pipeline_thresholds",
        {
            "paper_gate": {"max_drawdown_pct": 55},
            "retirement": {"max_drawdown_pct": 40},
            "decay": {"degradation_threshold": 35},
        },
    )

    cfg = load_pipeline_config()
    assert abs(float(cfg["paper_gate"]["max_drawdown_pct"]) - 0.55) < 1e-9
    assert abs(float(cfg["retirement"]["max_drawdown_pct"]) - 0.40) < 1e-9
    assert abs(float(cfg["decay"]["degradation_threshold"]) - 0.35) < 1e-9

    healed = kv_get("axiom:pipeline_thresholds", {})
    assert abs(float(healed["paper_gate"]["max_drawdown_pct"]) - 0.55) < 1e-9
    assert abs(float(healed["retirement"]["max_drawdown_pct"]) - 0.40) < 1e-9
    assert abs(float(healed["decay"]["degradation_threshold"]) - 0.35) < 1e-9


# --- M-2 (2026-06-09 audit): robustness_thresholds.wfa_fold_pass_rate_min ----
# Was read as a raw float with no normalization, so an operator entering 60
# (whole percent, the convention every other ratio threshold accepts) made the
# WFA fold gate unsatisfiable (pass_rate <= 1.0 < 60).


def test_wfa_fold_pass_rate_min_accepts_percent_points_and_fraction_identically(AXIOM_db):
    save_pipeline_config({"robustness_thresholds": {"wfa_fold_pass_rate_min": 60}})
    as_percent = float(load_pipeline_config()["robustness_thresholds"]["wfa_fold_pass_rate_min"])

    save_pipeline_config({"robustness_thresholds": {"wfa_fold_pass_rate_min": 0.60}})
    as_fraction = float(load_pipeline_config()["robustness_thresholds"]["wfa_fold_pass_rate_min"])

    assert abs(as_percent - 0.60) < 1e-9
    assert abs(as_fraction - 0.60) < 1e-9
    assert as_percent == as_fraction


def test_wfa_fold_pass_rate_min_clamps_to_unit_interval(AXIOM_db):
    save_pipeline_config({"robustness_thresholds": {"wfa_fold_pass_rate_min": 250}})
    cfg = load_pipeline_config()
    assert float(cfg["robustness_thresholds"]["wfa_fold_pass_rate_min"]) <= 1.0

    save_pipeline_config({"robustness_thresholds": {"wfa_fold_pass_rate_min": -3}})
    cfg = load_pipeline_config()
    assert float(cfg["robustness_thresholds"]["wfa_fold_pass_rate_min"]) >= 0.0


def test_wfa_fold_gate_passes_with_legacy_percent_point_floor(AXIOM_db):
    # Legacy raw kv payload (bypasses save normalization) with the floor stored
    # as whole percent. 2/3 positive OOS folds (0.667) must satisfy a 60% floor.
    kv_set(
        "axiom:pipeline_thresholds",
        {"robustness_thresholds": {"wfa_fold_pass_rate_min": 60}},
    )

    metrics = {
        "status": "completed",
        "splits": [
            {"out_of_sample": {"sharpe": 1.1}},
            {"out_of_sample": {"sharpe": 0.4}},
            {"out_of_sample": {"sharpe": -0.2}},
        ],
    }
    payload = _validation_row_to_verdict_payload("walk_forward", metrics, {})

    assert abs(payload["pass_rate"] - (2.0 / 3.0)) < 1e-9
    assert payload["passed"] is True
    assert payload["status"] == "pass"
