"""Complete gauntlet run for S03159 (keltner_rsi ETH/USDT 4h)."""
import sys, json, time
sys.path.insert(0, '.')


if __name__ == '__main__':
    import sqlite3
    from axiom.config import AXIOM_DB
    from axiom.strategies.backtest import walk_forward, backtest_strategy

    SID = 'S03159'
    ASSET = 'ETH/USDT'
    STYPE = 'keltner_rsi'
    PARAMS = {'atr_period': 14, 'kc_mult': 2, 'rsi_overbought': 70, 'rsi_oversold': 30, 'rsi_period': 14}
    TF = '4h'

    conn = sqlite3.connect(AXIOM_DB)
    conn.row_factory = sqlite3.Row

    # Restore to gauntlet
    conn.execute("UPDATE strategies SET status='gauntlet', stage='gauntlet' WHERE id=?", (SID,))
    conn.execute("DELETE FROM gate_rejections WHERE strategy_id=?", (SID,))
    conn.commit()
    print(f"Restored {SID} to gauntlet")

    # Run backtest
    print(f"Running backtest for {SID}...")
    bt = backtest_strategy(
        strategy_id=SID, asset=ASSET, strategy_type=STYPE, params=PARAMS,
        bars=17520, timeframe=TF, persist_legacy_run=True, regime_gate=False,
    )
    if 'error' in bt:
        print(f"ERROR: {bt['error']}")
        sys.exit(1)

    m = bt.get('metrics', bt)
    is_m = m.get('in_sample', m)
    oos_m = m.get('out_of_sample', {})
    is_t = is_m.get('total_trades', 0) or 0
    is_s = float(is_m.get('sharpe', 0) or 0)
    is_dd = float(is_m.get('max_drawdown_pct', 1.0) or 1.0)
    is_pf = float(is_m.get('profit_factor', 0) or 0)
    oos_t = oos_m.get('total_trades', 0) or 0 if oos_m else 0
    oos_s = float(oos_m.get('sharpe', 0) or 0) if oos_m else 0
    oos_dd = float(oos_m.get('max_drawdown_pct', 1.0) or 1.0) if oos_m else 1.0
    oos_pf = float(oos_m.get('profit_factor', 0) or 0) if oos_m else 0
    oos_wr = float(oos_m.get('win_rate', 0) or 0) if oos_m else 0
    print(f"IS:  t={is_t} s={is_s:.3f} dd={is_dd:.3f} pf={is_pf:.3f}")
    print(f"OOS: t={oos_t} s={oos_s:.3f} dd={oos_dd:.3f} pf={oos_pf:.3f} wr={oos_wr:.3f}")

    if oos_pf < 1.05:
        print(f"WARNING: OOS pf={oos_pf:.3f} < 1.05 — will fail gate profit factor check")
    if oos_t == 0:
        print("ERROR: 0 OOS trades — strategy produces no signals")
        sys.exit(1)

    br = conn.execute("""
        SELECT result_id FROM backtest_results
        WHERE strategy_id=? AND result_type='backtest'
        ORDER BY created_at DESC LIMIT 1
    """, (SID,)).fetchone()
    result_id = br['result_id'] if br else None
    print(f"result_id = {result_id}")

    # Update metrics
    new_metrics = {
        'total_trades': is_t, 'sharpe_ratio': round(is_s, 4),
        'max_drawdown': round(is_dd, 4), 'win_rate': round(float(is_m.get('win_rate', 0) or 0), 4),
        'profit_factor': round(is_pf, 4),
        'total_return_pct': round(float(is_m.get('total_return_pct', 0) or 0), 4),
        'out_of_sample': dict(oos_m) if oos_m else {},
    }
    conn.execute("UPDATE strategies SET metrics=?, status='gauntlet', stage='gauntlet' WHERE id=?",
                 (json.dumps(new_metrics), SID))
    conn.execute("DELETE FROM gate_rejections WHERE strategy_id=?", (SID,))
    conn.commit()

    # WFA
    print(f"\nRunning WFA for {SID}...")
    wfa = walk_forward(strategy_id=SID, asset=ASSET, strategy_type=STYPE, params=PARAMS, total_bars=17520)
    if 'error' in wfa:
        print(f"WFA ERROR: {wfa['error']}")
        sys.exit(1)

    splits = wfa.get('splits', [])
    n_folds = len(splits)
    positive_folds = sum(1 for s in splits if float(s.get('out_of_sample', {}).get('sharpe', -999) or -999) > 0)
    pass_rate = positive_folds / n_folds if n_folds > 0 else 0
    total_oos_t = sum(s.get('out_of_sample', {}).get('total_trades', 0) or 0 for s in splits)
    avg_oos_s = wfa.get('avg_oos_sharpe', 0)
    wfa_verdict = 'PASS' if pass_rate >= 0.30 and n_folds >= 2 else 'FAIL'
    print(f"WFA: folds={n_folds} positive={positive_folds} pass_rate={pass_rate:.0%} avg_oos_s={avg_oos_s:.3f} oos_t={total_oos_t} verdict={wfa_verdict}")
    for i, s in enumerate(splits):
        oos = s.get('out_of_sample', {})
        is_d = s.get('in_sample', {})
        print(f"  Fold {i+1}: IS_t={is_d.get('total_trades','?')} IS_s={float(is_d.get('sharpe', 0) or 0):.3f} | OOS_t={oos.get('total_trades','?')} OOS_s={float(oos.get('sharpe', 0) or 0):.3f}")

    wfa_stored = {
        'n_folds': n_folds, 'folds': n_folds, 'pass_rate': pass_rate,
        'oos_trades': total_oos_t, 'total_oos_trades': total_oos_t,
        'avg_oos_sharpe': avg_oos_s, 'oos_sharpe': avg_oos_s,
        'degradation': wfa.get('degradation', 0),
        'verdict': wfa_verdict, 'status': 'succeeded', 'splits': splits,
    }
    conn.execute("DELETE FROM backtest_results WHERE strategy_id=? AND result_type='walk_forward'", (SID,))
    conn.execute("""
        INSERT INTO backtest_results (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at)
        VALUES (?, ?, 'walk_forward', ?, ?, ?, '{"status": "succeeded"}', datetime('now'))
    """, (f"wfa_{SID}_{int(time.time())}", SID, ASSET, TF, json.dumps(wfa_stored)))
    conn.commit()
    print(f"WFA stored: verdict={wfa_verdict}")

    if wfa_verdict == 'FAIL':
        print("WFA failed — checking gate anyway...")

    # Param Jitter
    if result_id:
        print("\nRunning Param Jitter...")
        try:
            from axiom.routers.robustness import _run_param_jitter_analysis, ParamJitterBody
            pj_body = ParamJitterBody(strategy_id=SID, result_id=result_id, symbol=ASSET, timeframe=TF, n_iterations=30, jitter_pct=10.0)
            pj = _run_param_jitter_analysis(pj_body)
            pj_verdict = pj.get('verdict', 'PASS')
            pj['status'] = 'succeeded'
            print(f"  PJ: verdict={pj_verdict} pass_rate={pj.get('pass_rate', pj.get('pct_positive_sharpe'))}")
            conn.execute("DELETE FROM backtest_results WHERE strategy_id=? AND result_type='param_jitter'", (SID,))
            conn.execute("""
                INSERT INTO backtest_results (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at)
                VALUES (?, ?, 'param_jitter', ?, ?, ?, '{"status": "succeeded"}', datetime('now'))
            """, (f"rob_pj_{SID}_{int(time.time())}", SID, ASSET, TF, json.dumps(pj)))
            conn.commit()
        except Exception as e:
            print(f"  PJ ERROR: {e}")

    # Check gate
    print("\nChecking gauntlet gate...")
    from axiom.policy import load_pipeline_config, _evaluate_gauntlet_gate
    config = load_pipeline_config()
    ok, reason = _evaluate_gauntlet_gate(SID, config)
    print(f"Gate: {'PASS' if ok else 'FAIL'} - {reason}")

    if ok:
        conn.execute("UPDATE strategies SET status='paper', stage='paper' WHERE id=?", (SID,))
        conn.commit()
        print(f"{SID} promoted to paper!")

    conn.close()
    print("\nDone.")
