"""Settings UI percent contract: ratio thresholds display as whole percent.

The policy config canonically stores drawdown-style thresholds as fractions
(0.30); the settings page presents them with a "%" unit. The conversion to
whole percent happens exactly once, at the settings read boundary — these
tests pin that contract so a stored 0.5 can never again render as "0.5 %"
while meaning 50%.
"""

from axiom.policy import (
    _UI_PERCENT_THRESHOLD_PATHS,
    _coerce_ratio_threshold,
    pipeline_thresholds_for_display,
)


def _sample_config():
    return {
        "quick_screen": {"max_drawdown_pct": 0.30, "min_total_return_pct": 0.01, "min_sharpe": 0.5},
        "gauntlet": {"max_drawdown_pct": 0.5, "min_sharpe": 0.1},
        "paper_trading": {"max_drawdown_pct": 0.15},
        "live_graduated": {"decay_kill_switch_pct": 0.30},
    }


class TestPipelineThresholdsForDisplay:
    def test_fractions_become_whole_percent(self):
        display = pipeline_thresholds_for_display(_sample_config())
        assert display["quick_screen"]["max_drawdown_pct"] == 30.0
        assert display["gauntlet"]["max_drawdown_pct"] == 50.0
        assert display["paper_trading"]["max_drawdown_pct"] == 15.0
        assert display["live_graduated"]["decay_kill_switch_pct"] == 30.0

    def test_non_ratio_fields_untouched(self):
        display = pipeline_thresholds_for_display(_sample_config())
        # min_total_return_pct is consumed as percent points — not a ratio path.
        assert display["quick_screen"]["min_total_return_pct"] == 0.01
        assert display["gauntlet"]["min_sharpe"] == 0.1

    def test_original_config_not_mutated(self):
        config = _sample_config()
        pipeline_thresholds_for_display(config)
        assert config["gauntlet"]["max_drawdown_pct"] == 0.5

    def test_already_whole_percent_values_pass_through(self):
        # Defensive: a >1 value is already percent points and must not be scaled.
        display = pipeline_thresholds_for_display({"gauntlet": {"max_drawdown_pct": 30}})
        assert display["gauntlet"]["max_drawdown_pct"] == 30

    def test_missing_sections_and_garbage_are_safe(self):
        assert pipeline_thresholds_for_display({}) == {}
        assert pipeline_thresholds_for_display(None) == {}
        display = pipeline_thresholds_for_display({"gauntlet": {"max_drawdown_pct": "oops"}})
        assert display["gauntlet"]["max_drawdown_pct"] == "oops"


class TestWholePercentRoundTrip:
    def test_ui_whole_percent_write_normalizes_to_fraction(self):
        # The UI writes 30 (whole percent); the policy loader must read 0.30.
        assert _coerce_ratio_threshold(30, 0.25) == 0.30

    def test_legacy_fraction_write_still_normalizes(self):
        assert _coerce_ratio_threshold(0.30, 0.25) == 0.30

    def test_every_ui_percent_path_round_trips(self):
        for _section, _field in _UI_PERCENT_THRESHOLD_PATHS:
            stored_whole = 40
            fraction = _coerce_ratio_threshold(stored_whole, 0.25)
            display = pipeline_thresholds_for_display({_section: {_field: fraction}})
            assert display[_section][_field] == 40.0
