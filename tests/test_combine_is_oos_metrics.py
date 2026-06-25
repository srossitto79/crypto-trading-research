from __future__ import annotations

import math

from axiom.strategy_lifecycle import combine_is_oos_metrics, _normalize_history_metrics


def _is_block(**overrides):
    base = {
        "total_trades": 60,
        "wins": 15,
        "losses": 45,
        "breakeven_trades": 0,
        "gross_profit": 1.20,
        "gross_loss": 1.80,
        "win_rate": 0.25,
        "profit_factor": 1.20 / 1.80,
        "total_return_pct": -0.10,
        "sharpe": -1.0,
        "max_drawdown_pct": 0.20,
        "avg_trade_pct": -0.001667,
        "avg_bars_held": 40.0,
        "backtest_months": 8.4,
        "annualized_return_pct": -0.140,
        "monthly_return_pct": -0.0119,
        "start_date": "2025-04-19T17:00:00+00:00",
        "end_date": "2025-12-30T20:00:00+00:00",
    }
    base.update(overrides)
    return base


def _oos_block(**overrides):
    base = {
        "total_trades": 20,
        "wins": 7,
        "losses": 13,
        "breakeven_trades": 0,
        "gross_profit": 0.60,
        "gross_loss": 0.50,
        "win_rate": 0.35,
        "profit_factor": 0.60 / 0.50,
        "total_return_pct": 0.05,
        "sharpe": 0.5,
        "max_drawdown_pct": 0.15,
        "avg_trade_pct": 0.0025,
        "avg_bars_held": 50.0,
        "backtest_months": 3.6,
        "annualized_return_pct": 0.175,
        "monthly_return_pct": 0.0139,
        "start_date": "2025-12-30T21:00:00+00:00",
        "end_date": "2026-04-19T16:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_counts_and_ratios_are_exact_sums():
    combined = combine_is_oos_metrics(_is_block(), _oos_block())
    assert combined["total_trades"] == 80
    assert combined["wins"] == 22
    assert combined["losses"] == 58
    assert combined["breakeven_trades"] == 0
    assert math.isclose(combined["gross_profit"], 1.80, rel_tol=1e-9)
    assert math.isclose(combined["gross_loss"], 2.30, rel_tol=1e-9)
    assert math.isclose(combined["win_rate"], 22 / 80, rel_tol=1e-9)
    assert math.isclose(combined["profit_factor"], 1.80 / 2.30, rel_tol=1e-9)


def test_total_return_is_compounded():
    combined = combine_is_oos_metrics(_is_block(), _oos_block())
    expected = (1 + -0.10) * (1 + 0.05) - 1
    assert math.isclose(combined["total_return_pct"], expected, rel_tol=1e-9)


def test_cagr_uses_combined_duration():
    combined = combine_is_oos_metrics(_is_block(), _oos_block())
    assert math.isclose(combined["backtest_months"], 12.0, rel_tol=1e-9)
    expected_cagr = (1 + combined["total_return_pct"]) ** (12.0 / 12.0) - 1
    assert math.isclose(combined["annualized_return_pct"], expected_cagr, rel_tol=1e-9)


def test_weighted_averages_track_trade_count():
    combined = combine_is_oos_metrics(_is_block(), _oos_block())
    expected_avg_trade = (-0.001667 * 60 + 0.0025 * 20) / 80
    assert math.isclose(combined["avg_trade_pct"], expected_avg_trade, rel_tol=1e-6)
    expected_avg_bars = (40.0 * 60 + 50.0 * 20) / 80
    assert math.isclose(combined["avg_bars_held"], expected_avg_bars, rel_tol=1e-9)


def test_sharpe_is_month_weighted_and_flagged_approximate():
    combined = combine_is_oos_metrics(_is_block(), _oos_block())
    expected_sharpe = (-1.0 * 8.4 + 0.5 * 3.6) / 12.0
    assert math.isclose(combined["sharpe"], expected_sharpe, rel_tol=1e-9)
    assert combined["sharpe_is_approximation"] is True


def test_sharpe_reliable_uses_combined_trade_count():
    # OOS alone has only 7 trades (below the 20-trade threshold), but the
    # combined window has 21 trades — reliability must reflect the combined
    # count so the full-window Sharpe isn't hidden behind an em dash in the UI.
    combined = combine_is_oos_metrics(_is_block(total_trades=14), _oos_block(total_trades=7))
    assert combined["total_trades"] == 21
    assert combined["sharpe_is_reliable"] is True


def test_sharpe_reliable_false_when_combined_below_threshold():
    combined = combine_is_oos_metrics(_is_block(total_trades=10), _oos_block(total_trades=5))
    assert combined["total_trades"] == 15
    assert combined["sharpe_is_reliable"] is False


def test_max_dd_is_max_of_halves_and_flagged_approximate():
    combined = combine_is_oos_metrics(_is_block(max_drawdown_pct=0.30), _oos_block(max_drawdown_pct=0.10))
    assert math.isclose(combined["max_drawdown_pct"], 0.30, rel_tol=1e-9)
    assert combined["max_drawdown_is_approximation"] is True


def test_start_end_dates_span_full_window():
    combined = combine_is_oos_metrics(_is_block(), _oos_block())
    assert combined["start_date"] == "2025-04-19T17:00:00+00:00"
    assert combined["end_date"] == "2026-04-19T16:00:00+00:00"


def test_handles_empty_inputs_without_dividing_by_zero():
    assert combine_is_oos_metrics({}, {}) == {}
    only_is = combine_is_oos_metrics(_is_block(), {})
    assert only_is["total_trades"] == 60
    assert math.isclose(only_is["total_return_pct"], -0.10, rel_tol=1e-9)
    only_oos = combine_is_oos_metrics({}, _oos_block())
    assert only_oos["total_trades"] == 20
    assert math.isclose(only_oos["total_return_pct"], 0.05, rel_tol=1e-9)


def test_zero_trades_gives_zero_ratios_not_nan():
    empty_is = {"total_trades": 0, "wins": 0, "losses": 0, "gross_profit": 0.0, "gross_loss": 0.0, "backtest_months": 8.4, "total_return_pct": 0.0}
    empty_oos = {"total_trades": 0, "wins": 0, "losses": 0, "gross_profit": 0.0, "gross_loss": 0.0, "backtest_months": 3.6, "total_return_pct": 0.0}
    combined = combine_is_oos_metrics(empty_is, empty_oos)
    assert combined["win_rate"] == 0.0
    assert combined["profit_factor"] == 0.0
    assert combined["avg_trade_pct"] == 0.0


def test_normalize_history_metrics_overwrites_legacy_oos_scalars_with_combined():
    raw = {
        "in_sample": _is_block(),
        "out_of_sample": _oos_block(),
        # Legacy OOS-flattened top-level scalars (what backtest.py writes today):
        "total_trades": 20,
        "win_rate": 0.35,
        "profit_factor": 1.20,
        "sharpe": 0.5,
        "max_drawdown_pct": 0.15,
        "total_return_pct": 0.05,
    }
    normalized = _normalize_history_metrics(raw)
    # Scalars now reflect combined (full window):
    assert normalized["total_trades"] == 80
    assert math.isclose(normalized["win_rate"], 22 / 80, rel_tol=1e-9)
    assert math.isclose(normalized["profit_factor"], 1.80 / 2.30, rel_tol=1e-9)
    # OOS-specific keys expose the right-side columns:
    assert math.isclose(normalized["out_of_sample_sharpe"], 0.5, rel_tol=1e-9)
    assert math.isclose(normalized["out_of_sample_annualized_return_pct"], 0.175, rel_tol=1e-9)
    # IS/OOS nested dicts are preserved untouched:
    assert normalized["in_sample"]["total_trades"] == 60
    assert normalized["out_of_sample"]["total_trades"] == 20
    # Approximation flags are present so the UI can render a tooltip:
    assert normalized["sharpe_is_approximation"] is True
    assert normalized["max_drawdown_is_approximation"] is True
    # Dedicated combined block is available:
    assert normalized["combined"]["total_trades"] == 80
