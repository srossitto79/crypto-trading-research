"""Test seed strategy variants to find best configs."""
import sys, json
sys.path.insert(0, '.')

SEEDS = [
    # S03090 without adx_max override (will use default regime filter)
    ('S03090-v2', 'ETH/USDT', 'zscore_reversion', {'period': 20, 'entry_threshold': 1.8, 'exit_threshold': 0.3}, '1h'),
    # S03099 stochastic ETH 1h
    ('S03099', 'ETH/USDT', 'stochastic', {'k_period': 14, 'k_overbought': 80, 'k_oversold': 20, 'd_period': 3}, '1h'),
    # Try zscore on BTC 1d (originally supposed to have 37 passing)
    ('S03090-1d', 'BTC/USDT', 'zscore_reversion', {'period': 20, 'entry_threshold': 1.5, 'exit_threshold': 0.5}, '1d'),
    # EMA cross BTC 1h (seed 3 from plan)
    ('S03093-v2', 'BTC/USDT', 'ema_cross', {'fast_period': 12, 'slow_period': 26}, '1h'),
]


if __name__ == '__main__':
    from axiom.strategies.backtest import backtest_strategy

    for sid, asset, stype, params, tf in SEEDS:
        print(f"\n{'='*60}")
        print(f"Testing {sid} ({stype} {asset} {tf}) params={params}...")
        result = backtest_strategy(
            strategy_id='S03090',  # use existing strategy ID for data loading
            asset=asset,
            strategy_type=stype,
            params=params,
            bars=17520,
            timeframe=tf,
            persist_legacy_run=False,
            regime_gate=True,  # use regime gate to match real pipeline
        )
        if 'error' in result:
            print(f"  ERROR: {result['error']}")
            continue

        m = result.get('metrics', result)
        is_m = m.get('in_sample', m)
        oos_m = m.get('out_of_sample', {})

        is_trades = is_m.get('total_trades', 0) or 0
        is_sharpe = float(is_m.get('sharpe', 0) or 0)
        is_dd = float(is_m.get('max_drawdown_pct', 1.0) or 1.0)
        is_pf = float(is_m.get('profit_factor', 0) or 0)
        is_wr = float(is_m.get('win_rate', 0) or 0)

        print(f"  IS: trades={is_trades} sharpe={is_sharpe:.3f} dd={is_dd:.3f} pf={is_pf:.3f} wr={is_wr:.3f}")

        if oos_m:
            oos_trades = oos_m.get('total_trades', 0) or 0
            oos_sharpe = float(oos_m.get('sharpe', 0) or 0)
            oos_dd = float(oos_m.get('max_drawdown_pct', 1.0) or 1.0)
            oos_pf = float(oos_m.get('profit_factor', 0) or 0)
            oos_wr = float(oos_m.get('win_rate', 0) or 0)
            print(f"  OOS: trades={oos_trades} sharpe={oos_sharpe:.3f} dd={oos_dd:.3f} pf={oos_pf:.3f} wr={oos_wr:.3f}")

            # Flag if viable
            viable = oos_trades >= 10 and oos_sharpe > 0.3 and oos_pf > 1.05 and oos_dd < 0.5
            if viable:
                print(f"  *** VIABLE CANDIDATE ***")

    print("\nDone.")
