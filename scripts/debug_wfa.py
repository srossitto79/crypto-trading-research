"""Debug WFA result structure for S03097."""
import sys, json
sys.path.insert(0, '.')

if __name__ == '__main__':
    from axiom.strategies.backtest import walk_forward

    print("Running WFA for S03097 (engulfing BTC/4h)...")
    result = walk_forward(
        strategy_id='S03097',
        asset='BTC/USDT',
        strategy_type='engulfing',
        params={'volume_mult': 1.8, 'atr_period': 14},
        total_bars=17520,
    )

    if 'error' in result:
        print(f"ERROR: {result['error']}")
    else:
        # Print all top-level fields
        print("\nTop-level fields:")
        for k, v in result.items():
            if k != 'splits' and not isinstance(v, (list, dict)):
                print(f"  {k}: {v}")
            elif isinstance(v, list):
                print(f"  {k}: list of {len(v)}")

        # Print splits details
        splits = result.get('splits', [])
        print(f"\nSplits ({len(splits)} folds):")
        for i, s in enumerate(splits):
            print(f"\nFold {i+1}:")
            for k, v in s.items():
                if not isinstance(v, dict):
                    print(f"    {k}: {v}")
                else:
                    print(f"    {k}: {json.dumps(v)[:100]}")

    print("\nDone.")
