from axiom.task_timeouts import (
    coerce_stale_recovery_minutes,
    recommended_agent_reaper_timeout_minutes,
    recommended_stale_recovery_minutes,
    resolve_agent_task_timeout_seconds,
)


def test_backtest_task_timeout_defaults_above_general_runtime():
    assert resolve_agent_task_timeout_seconds("research", settings={}) == 900
    assert resolve_agent_task_timeout_seconds("simulation", settings={}) == 1800


def test_recommended_recovery_window_exceeds_reaper_window():
    assert recommended_agent_reaper_timeout_minutes({}) == 31
    assert recommended_stale_recovery_minutes({}) == 36


def test_stale_recovery_minutes_clamp_to_safe_minimum():
    assert coerce_stale_recovery_minutes(7, settings={}) == 36
    assert coerce_stale_recovery_minutes(60, settings={}) == 60
