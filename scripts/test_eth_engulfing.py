"""Quick test: engulfing ETH/USDT 4h metrics."""
import sys, json
sys.path.insert(0, '.')


if __name__ == '__main__':
    from axiom.strategies.backtest import backtest_strategy

    for volume_mult in [1.5, 1.8, 2.0]:
        params = {'volume_mult': volume_mult, 'atr_period': 14}
        print(f"\nTesting engulfing ETH/USDT 4h volume_mult={volume_mult}...")
        bt = backtest_strategy(
            strategy_id='S03097',  # use BTC for data loading fallback
            asset='ETH/USDT',
            strategy_type='engulfing',
            params=params,
            bars=17520,
            timeframe='4h',
            persist_legacy_run=False,
            regime_gate=False,
        )
        if 'error' in bt:
            print(f"  ERROR: {bt['error']}")
            continue

        m = bt.get('metrics', bt)
        is_m = m.get('in_sample', m)
        oos_m = m.get('out_of_sample', {})
        print(f"  IS: t={is_m.get('total_trades',0)} s={float(is_m.get('sharpe',0) or 0):.3f} dd={float(is_m.get('max_drawdown_pct',1) or 1):.3f} pf={float(is_m.get('profit_factor',0) or 0):.3f}")
        if oos_m:
            print(f"  OOS: t={oos_m.get('total_trades',0)} s={float(oos_m.get('sharpe',0) or 0):.3f} dd={float(oos_m.get('max_drawdown_pct',1) or 1):.3f} pf={float(oos_m.get('profit_factor',0) or 0):.3f}")

    print("\nDone.")
