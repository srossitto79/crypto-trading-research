"""Tests for data-quality metric invariants and runtime loadability hygiene.

These guard against the failure mode where an engine/data bug produces
implausible metrics (e.g. a zeroed in-sample leg) and the pipeline silently
consumes them as legitimate gate failures, or where a strategy with an
unloadable runtime sits in an active stage forever.
"""

from axiom.metrics_integrity import (
    DATA_QUALITY_HOLD_PREFIX,
    check_metrics_integrity,
    data_quality_hold_reason,
)


def _metrics(is_trades, oos_trades, **extra):
    payload = {
        "in_sample": {"total_trades": is_trades, "sharpe": 0.0},
        "out_of_sample": {"total_trades": oos_trades, "sharpe": -1.2},
        "total_trades": oos_trades,
    }
    payload.update(extra)
    return payload


class TestCheckMetricsIntegrity:
    def test_zero_is_with_active_oos_is_anomalous(self):
        # The 2026-06 dropna regression signature: IS leg erased, OOS active.
        anomalies = check_metrics_integrity(_metrics(0, 58))
        assert len(anomalies) == 1
        assert "in_sample reports 0 trades" in anomalies[0]

    def test_zero_oos_with_active_is_is_anomalous(self):
        anomalies = check_metrics_integrity(_metrics(45, 0))
        assert len(anomalies) == 1
        assert "out_of_sample reports 0 trades" in anomalies[0]

    def test_healthy_metrics_pass(self):
        assert check_metrics_integrity(_metrics(120, 40)) == []

    def test_both_zero_is_plausible(self):
        # A strategy that never trades is bad, not anomalous — gates handle it.
        assert check_metrics_integrity(_metrics(0, 0)) == []

    def test_quiet_oos_below_active_is_threshold_passes(self):
        # 0 OOS trades with a modest IS count can be a legitimate quiet regime.
        assert check_metrics_integrity(_metrics(15, 0)) == []

    def test_zero_is_with_few_oos_trades_passes(self):
        # A handful of OOS trades is not enough evidence of a lost IS leg.
        assert check_metrics_integrity(_metrics(0, 3)) == []

    def test_missing_nested_blocks_pass(self):
        assert check_metrics_integrity({"total_trades": 50, "sharpe": 1.0}) == []
        assert check_metrics_integrity({}) == []
        assert check_metrics_integrity(None) == []
        assert check_metrics_integrity("garbage") == []

    def test_non_numeric_trade_counts_pass(self):
        payload = {
            "in_sample": {"total_trades": "n/a"},
            "out_of_sample": {"total_trades": 50},
        }
        assert check_metrics_integrity(payload) == []

    def test_hold_reason_has_prefix_and_no_reject_marker(self):
        anomalies = check_metrics_integrity(_metrics(0, 58))
        reason = data_quality_hold_reason(anomalies)
        assert reason.startswith(DATA_QUALITY_HOLD_PREFIX)
        # "(reject)" gate text terminally archives via the hygiene sweep — a
        # data-quality hold must never carry it.
        assert "(reject)" not in reason


class TestGuardrailQuarantine:
    def test_quick_screen_guardrails_hold_anomalous_metrics(self):
        from axiom.brain import _quick_screen_overfitting_guardrails

        can_proceed, reason = _quick_screen_overfitting_guardrails(_metrics(0, 58))
        assert can_proceed is False
        assert reason.startswith(DATA_QUALITY_HOLD_PREFIX)
        assert "(reject)" not in reason

    def test_gauntlet_entry_guardrails_hold_anomalous_metrics(self):
        from axiom.brain import _gauntlet_entry_guardrails

        can_proceed, reason = _gauntlet_entry_guardrails("S-test", _metrics(0, 58))
        assert can_proceed is False
        assert reason.startswith(DATA_QUALITY_HOLD_PREFIX)
        assert "(reject)" not in reason


class TestSweepTreatsHoldAsNonTerminal:
    def test_data_quality_hold_is_not_terminal(self):
        from axiom.evolution import _is_terminal_quick_screen_gate_failure

        reason = (
            "quick_screen→gauntlet blocked: "
            + data_quality_hold_reason(check_metrics_integrity(_metrics(0, 58)))
        )
        assert _is_terminal_quick_screen_gate_failure(reason) is False

    def test_data_quality_hold_overrides_other_reject_text(self):
        from axiom.evolution import _is_terminal_quick_screen_gate_failure

        combined = "DataQualityHold: in_sample lost; Gate5: Trades 0 < 30 (reject)"
        assert _is_terminal_quick_screen_gate_failure(combined) is False

    def test_plain_reject_text_is_still_terminal(self):
        from axiom.evolution import _is_terminal_quick_screen_gate_failure

        assert _is_terminal_quick_screen_gate_failure(
            "quick_screen→gauntlet blocked: Gate5: Trades 0 < 30 (reject)"
        ) is True


class TestRuntimeLoadability:
    def test_registered_builtin_type_resolves(self):
        from axiom.strategies.registry import runtime_unloadable_reason

        assert runtime_unloadable_reason("rsi_momentum", None) is None

    def test_unregistered_type_reports_reason(self):
        from axiom.strategies.registry import runtime_unloadable_reason

        reason = runtime_unloadable_reason(
            "definitely_not_a_real_strategy_type_xyz",
            "definitely_not_a_real_runtime_xyz",
        )
        assert reason is not None
        assert "not registered" in reason or "could not be resolved" in reason

    def test_missing_both_types_reports_reason(self):
        from axiom.strategies.registry import runtime_unloadable_reason

        assert runtime_unloadable_reason(None, "") is not None

    def test_evolution_helper_delegates(self):
        from axiom.evolution import _runtime_unloadable_reason

        assert _runtime_unloadable_reason("rsi_momentum", None) is None
        assert _runtime_unloadable_reason("nope_xyz", "nope_xyz") is not None
