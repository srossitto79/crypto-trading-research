from __future__ import annotations

from axiom.regime import RANGE_BOUND, resolve_regime_gate


def test_backtest_module_imports_with_regime_gate_available():
    import axiom.strategies.backtest as backtest_mod

    assert callable(backtest_mod.resolve_regime_gate)


def test_resolve_regime_gate_defaults_williams_r_to_range_bound_cap():
    compatible, adx_min, adx_cap = resolve_regime_gate(
        "williams_r",
        {
            "williams_r_period": 14,
            "williams_r_oversold": -80,
            "williams_r_overbought": -20,
        },
    )

    assert compatible == {RANGE_BOUND}
    assert adx_min is None
    assert adx_cap == 25.0
