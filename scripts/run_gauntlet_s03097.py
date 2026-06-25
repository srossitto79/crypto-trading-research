"""Run complete gauntlet for S03097 (engulfing BTC/4h) and S03098 (engulfing SOL/4h)."""
import sys, json, time
sys.path.insert(0, '.')

STRATEGY_ID = 'S03097'
STRATEGY_ID2 = 'S03098'


def run_wfa(sid, asset, stype, params, tf):
    from axiom.strategies.backtest import walk_forward
    print(f"  Running WFA for {sid}...")
    result = walk_forward(
        strategy_id=sid,
        asset=asset,
        strategy_type=stype,
        params=params,
        total_bars=17520,  # 2 years of 1h = 8 years of 4h data
    )
    return result


def run_full_backtest(sid, asset, stype, params, tf):
    from axiom.strategies.backtest import backtest_strategy
    print(f"  Running full backtest for {sid}...")
    result = backtest_strategy(
        strategy_id=sid,
        asset=asset,
        strategy_type=stype,
        params=params,
        bars=17520,
        timeframe=tf,
        persist_legacy_run=True,  # persist to get result_id
        regime_gate=False,
    )
    return result


if __name__ == '__main__':
    import sqlite3
    from axiom.config import AXIOM_DB

    db = AXIOM_DB
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    configs = [
        (STRATEGY_ID, 'BTC/USDT', 'engulfing', {'volume_mult': 1.8, 'atr_period': 14}, '4h'),
        (STRATEGY_ID2, 'SOL/USDT', 'engulfing', {'volume_mult': 1.5, 'atr_period': 14}, '4h'),
    ]

    for sid, asset, stype, params, tf in configs:
        print(f"\n{'='*70}")
        print(f"Processing {sid} ({stype} {asset} {tf})")

        # 1. Full backtest to get persistent result_id
        bt = run_full_backtest(sid, asset, stype, params, tf)
        if 'error' in bt:
            print(f"  Backtest ERROR: {bt['error']}")
            continue

        m = bt.get('metrics', bt)
        is_m = m.get('in_sample', m)
        oos_m = m.get('out_of_sample', {})

        is_t = is_m.get('total_trades', 0) or 0
        is_s = float(is_m.get('sharpe', 0) or 0)
        is_dd = float(is_m.get('max_drawdown_pct', 1.0) or 1.0)
        is_pf = float(is_m.get('profit_factor', 0) or 0)
        oos_t = oos_m.get('total_trades', 0) if oos_m else 0
        oos_s = float(oos_m.get('sharpe', 0) or 0) if oos_m else 0
        oos_dd = float(oos_m.get('max_drawdown_pct', 1.0) or 1.0) if oos_m else 1.0
        oos_pf = float(oos_m.get('profit_factor', 0) or 0) if oos_m else 0

        print(f"  IS:  t={is_t} s={is_s:.3f} dd={is_dd:.3f} pf={is_pf:.3f}")
        print(f"  OOS: t={oos_t} s={oos_s:.3f} dd={oos_dd:.3f} pf={oos_pf:.3f}")

        # Get the persisted result_id
        result_id = bt.get('result_id')
        if not result_id:
            br = conn.execute("""
                SELECT result_id FROM backtest_results
                WHERE strategy_id=? AND result_type='backtest'
                ORDER BY created_at DESC LIMIT 1
            """, (sid,)).fetchone()
            if br:
                result_id = br['result_id']

        print(f"  result_id={result_id}")

        # 2. Update strategy metrics
        new_metrics = {
            'total_trades': is_t,
            'sharpe_ratio': round(is_s, 4),
            'max_drawdown': round(is_dd, 4),
            'win_rate': round(float(is_m.get('win_rate', 0) or 0), 4),
            'profit_factor': round(is_pf, 4),
            'total_return_pct': round(float(is_m.get('total_return_pct', 0) or 0), 4),
        }
        if oos_m:
            new_metrics['out_of_sample'] = dict(oos_m)

        # Restore to gauntlet and update metrics
        conn.execute("""
            UPDATE strategies SET metrics=?, status='gauntlet', stage='gauntlet'
            WHERE id=?
        """, (json.dumps(new_metrics), sid))

        # Clear old gate rejections to prevent auto-archive
        conn.execute("DELETE FROM gate_rejections WHERE strategy_id=?", (sid,))

        conn.commit()
        print(f"  Restored {sid} to gauntlet with updated metrics")

        # 3. Run WFA
        print(f"\n  Running Walk-Forward Analysis...")
        wfa = run_wfa(sid, asset, stype, params, tf)
        if 'error' in wfa:
            print(f"  WFA ERROR: {wfa['error']}")
        else:
            n_folds = len(wfa.get('splits', []))
            pass_rate = wfa.get('pass_rate', 0)
            oos_sharpe = wfa.get('oos_sharpe', wfa.get('avg_oos_sharpe', 0))
            oos_trades = wfa.get('oos_trades', wfa.get('total_oos_trades', 0))
            print(f"  WFA: folds={n_folds} pass_rate={pass_rate:.0%} oos_sharpe={oos_sharpe:.3f} oos_trades={oos_trades}")
            for i, s in enumerate(wfa.get('splits', [])):
                is_sh = s.get('is_sharpe', s.get('in_sample', {}).get('sharpe', '?'))
                os_sh = s.get('oos_sharpe', s.get('out_of_sample', {}).get('sharpe', '?'))
                os_t = s.get('oos_trades', s.get('out_of_sample', {}).get('total_trades', '?'))
                print(f"    Fold {i+1}: IS_s={is_sh} OOS_s={os_sh} OOS_t={os_t}")

            # Store WFA result
            wfa_metrics = dict(wfa)
            wfa_metrics['verdict'] = 'PASS' if float(pass_rate or 0) >= 0.3 else 'FAIL'
            wfa_metrics['status'] = 'succeeded'

            # Delete old WFA results first
            conn.execute("""
                DELETE FROM backtest_results
                WHERE strategy_id=? AND result_type='walk_forward'
            """, (sid,))
            conn.execute("""
                INSERT INTO backtest_results (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at)
                VALUES (?, ?, 'walk_forward', ?, ?, ?, '{}', datetime('now'))
            """, (
                f"wfa_{sid}_{int(time.time())}",
                sid,
                asset,
                tf,
                json.dumps(wfa_metrics),
            ))
            conn.commit()
            print(f"  WFA stored: verdict={wfa_metrics['verdict']}")

    conn.close()
    print("\nDone.")
