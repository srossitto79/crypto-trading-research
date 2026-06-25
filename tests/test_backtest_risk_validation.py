from axiom.strategies.backtest import backtest_strategy, walk_forward


def test_backtest_strategy_rejects_unsupported_risk_controls(AXIOM_db):
    result = backtest_strategy(
        strategy_id="bt-risk-validation",
        asset="BTC",
        strategy_type="rsi_momentum",
        params={"stop_loss_pct": 2.0, "risk_pct": 0.01},
        bars=240,
    )

    warning = str(result.get("warning") or result.get("error") or "")
    assert "stop_loss_pct" in warning
    assert "risk_pct" in warning


def test_walk_forward_rejects_unsupported_risk_controls(AXIOM_db):
    result = walk_forward(
        strategy_id="wf-risk-validation",
        asset="BTC",
        strategy_type="rsi_momentum",
        params={"min_risk_reward_ratio": 2.0},
        total_bars=500,
        n_splits=2,
    )

    warning = str(result.get("warning") or result.get("error") or "")
    assert "min_risk_reward_ratio" in warning
