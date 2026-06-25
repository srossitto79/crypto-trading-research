"""Regression tests for MATH-01, MATH-02, MATH-05 fixes from the Phase 1.5
math correctness audit (docs/superpowers/audits/2026-04-16-math-correctness.md).
"""

from __future__ import annotations

import math
import pytest

from axiom.db import init_db


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


def test_profit_factor_is_infinite_when_no_losses():
    """MATH-01: profit_factor with all winners returns inf, not 10.0 sentinel."""
    from axiom.policy import score_strategy

    metrics = {
        "total_trades": 3,
        "wins": 3,
        "losses": 0,
        "win_rate": 1.0,
        "sharpe": 1.5,
        "sortino": 2.0,
        "max_drawdown_pct": 0.05,
        "profit_factor": float("inf"),
        "profit_factor_is_infinite": True,
        "total_return_pct": 0.30,
    }
    score = score_strategy(metrics)
    # 3 trades < 5 → 30 pt penalty applied. Score should still be finite
    # (not NaN, not inf). pf_score capped at 100 so inf doesn't blow it up.
    assert math.isfinite(score), f"score must be finite, got {score}"
    assert 0.0 <= score <= 100.0, f"score out of range: {score}"


def test_score_strategy_rejects_single_trade_strategy():
    """MATH-02: single-trade strategies are rejected (Sharpe undefined)."""
    from axiom.policy import score_strategy

    metrics = {
        "total_trades": 1,
        "wins": 1,
        "losses": 0,
        "win_rate": 1.0,
        "sharpe": 99.0,  # nonsense from 1 trade — must not score
        "max_drawdown_pct": 0.0,
        "profit_factor": float("inf"),
    }
    assert score_strategy(metrics) == 0.0


def test_score_strategy_penalizes_low_trade_count():
    """MATH-02: <5 trades incurs -30 penalty in addition to scoring."""
    from axiom.policy import score_strategy

    weak = {
        "total_trades": 3,
        "wins": 2,
        "losses": 1,
        "win_rate": 0.667,
        "sharpe": 1.5,
        "max_drawdown_pct": 0.05,
        "profit_factor": 2.0,
    }
    strong = dict(weak)
    strong["total_trades"] = 50
    strong["wins"] = 33
    strong["losses"] = 17

    weak_score = score_strategy(weak)
    strong_score = score_strategy(strong)

    # Both produce a number; the weak one is at least 30 points worse from MATH-05 penalty.
    assert weak_score < strong_score, f"weak={weak_score} should be < strong={strong_score}"
    # The gap should be roughly the 30-pt penalty (allow for trade_score component differences).
    assert (strong_score - weak_score) >= 25.0


def test_validate_backtest_metrics_returns_penalty_value():
    """MATH-05: validate returns a non-zero penalty for shape weaknesses."""
    from axiom.policy import validate_backtest_metrics

    metrics = {
        "total_trades": 10,
        "max_drawdown_pct": 0.60,  # > 50% → 15-pt penalty
    }
    is_valid, penalty, reason = validate_backtest_metrics(metrics)
    assert is_valid is True
    assert penalty >= 15.0, f"expected >=15 pt penalty, got {penalty}"
    assert "MaxDD" in reason


def test_compute_metrics_emits_profit_factor_is_infinite_flag():
    """MATH-01: compute_metrics() exposes the new flag downstream."""
    from axiom.strategies.backtest import compute_metrics

    # Three winning trades, zero losing.
    trades = [
        {"pnl_pct": 0.05, "bars_held": 10},
        {"pnl_pct": 0.03, "bars_held": 8},
        {"pnl_pct": 0.02, "bars_held": 12},
    ]
    result = compute_metrics(trades, total_bars=1000, timeframe="1h")
    assert result["profit_factor_is_infinite"] is True
    assert math.isinf(result["profit_factor"])
