"""Run fresh backtests for seed strategies to get complete metrics."""
import sys, json
sys.path.insert(0, '.')

SEEDS = [
    ('S03090', 'ETH/USDT', 'zscore_reversion', {'period': 20, 'entry_threshold': 1.8, 'exit_threshold': 0.3, 'adx_max': 100}, '1h'),
    ('S03094', 'ETH/USDT', 'vwap_trend_momentum', {'rsi_overbought': 70, 'rsi_oversold': 40, 'vol_mult': 1.2, 'volume_ma': 20, 'vwap_period': 20}, '4h'),
    ('S03098', 'SOL/USDT', 'engulfing', {'volume_mult': 1.5, 'atr_period': 14}, '4h'),
    ('S03097', 'BTC/USDT', 'engulfing', {'volume_mult': 1.8, 'atr_period': 14}, '4h'),
]


if __name__ == '__main__':
    import sqlite3
    from axiom.strategies.backtest import backtest_strategy
    from axiom.config import AXIOM_DB

    db = AXIOM_DB
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    for sid, asset, stype, params, tf in SEEDS:
        print(f"\n{'='*60}")
        print(f"Running {sid} ({stype} {asset} {tf})...")
        result = backtest_strategy(
            strategy_id=sid,
            asset=asset,
            strategy_type=stype,
            params=params,
            bars=17520,
            timeframe=tf,
            persist_legacy_run=False,
            regime_gate=False,
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
        is_ret = float(is_m.get('total_return_pct', 0) or 0)

        print(f"  IS: trades={is_trades} sharpe={is_sharpe:.3f} dd={is_dd:.3f} pf={is_pf:.3f} wr={is_wr:.3f}")

        if oos_m:
            oos_trades = oos_m.get('total_trades', 0) or 0
            oos_sharpe = float(oos_m.get('sharpe', 0) or 0)
            oos_dd = float(oos_m.get('max_drawdown_pct', 1.0) or 1.0)
            oos_pf = float(oos_m.get('profit_factor', 0) or 0)
            oos_wr = float(oos_m.get('win_rate', 0) or 0)
            print(f"  OOS: trades={oos_trades} sharpe={oos_sharpe:.3f} dd={oos_dd:.3f} pf={oos_pf:.3f} wr={oos_wr:.3f}")

        # Build new metrics
        new_metrics = {
            'total_trades': is_trades,
            'sharpe_ratio': round(is_sharpe, 4),
            'max_drawdown': round(is_dd, 4),
            'win_rate': round(is_wr, 4),
            'profit_factor': round(is_pf, 4),
            'total_return_pct': round(is_ret, 4),
        }
        if oos_m:
            new_metrics['out_of_sample'] = dict(oos_m)

        if is_trades > 0 and is_dd < 0.99:
            conn.execute("UPDATE strategies SET metrics=? WHERE id=?",
                         (json.dumps(new_metrics), sid))
            conn.commit()
            print(f"  OK Updated metrics for {sid}")
        else:
            print(f"  SKIP bad results: t={is_trades} dd={is_dd}")

    conn.close()
    print("\nDone.")
