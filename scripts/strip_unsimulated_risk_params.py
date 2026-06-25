#!/usr/bin/env python
"""Strip never-simulated optimizer-injected stop/TP values from strategies.params.

Audit lead B-4: Axiom/strategies/optimizer.py:_get_param_space used to inject
stop_loss_pct=[0.02, 0.03, 0.05, 0.08] and take_profit_pct=[0.04, 0.06, 0.10,
0.15] grids into every fallback parameter space. The backtest engine ignores
both fields inside ``params`` (warn-only, _UNSUPPORTED_BACKTEST_RISK_FIELDS),
so every grid value backtested byte-identically and the "winning" value was
never simulated. run_apply_optimized_defaults / evolution.apply_best_params
then merged that value into strategies.params, where the paper/live scanner
enforces it with PERCENT semantics — entry * (1 - 0.02/100) is a 0.02% stop,
below round-trip fees, i.e. guaranteed churn that no backtest ever validated.

This script removes stop_loss_pct / take_profit_pct from strategies.params,
but ONLY when the value is fraction-style and exactly matches the old overlay
grid (sl in {0.02, 0.03, 0.05, 0.08}, tp in {0.04, 0.06, 0.10, 0.15}). Each
key is judged independently — an author-chosen value like stop_loss_pct=0.025
or a percent-style 3.0 is never touched. The strategy class still receives its
own default_params at runtime, so classes that consume these fields internally
fall back to their authored defaults.

Non-destructive beyond the two keys; every removed value is logged so changes
are recoverable from the output. Idempotent. Dry-run by default.

Stop the app before --apply (start_all.ps1 supervises the backend; writing to
the live DB while it runs can race on the WAL lock).

Usage:
    python scripts/strip_unsimulated_risk_params.py            # dry-run report
    python scripts/strip_unsimulated_risk_params.py --apply    # write changes
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from axiom.db import get_db  # noqa: E402
from axiom.sim.clock import get_now  # noqa: E402

# The exact grids the optimizer used to inject (optimizer.py P2-3, removed by B-4).
OVERLAY_GRIDS: dict[str, tuple[float, ...]] = {
    "stop_loss_pct": (0.02, 0.03, 0.05, 0.08),
    "take_profit_pct": (0.04, 0.06, 0.10, 0.15),
}


def _matches_grid(value: object, grid: tuple[float, ...]) -> bool:
    """True when value is a fraction-style number exactly on the overlay grid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return any(math.isclose(float(value), g, rel_tol=0, abs_tol=1e-9) for g in grid)


def _candidates(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, type, symbol, stage, status, params
        FROM strategies
        WHERE params LIKE '%stop_loss_pct%' OR params LIKE '%take_profit_pct%'
        """
    ).fetchall()
    out = []
    for row in rows:
        strategy = dict(row)
        try:
            params = json.loads(strategy.get("params") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(params, dict):
            continue
        strip_keys = {
            key: params[key]
            for key, grid in OVERLAY_GRIDS.items()
            if key in params and _matches_grid(params.get(key), grid)
        }
        if not strip_keys:
            continue
        strategy["_params"] = params
        strategy["_strip"] = strip_keys
        out.append(strategy)
    return out


def _total_strategies(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    with get_db() as conn:
        total = _total_strategies(conn)
        if total == 0:
            print(
                "Refusing to run: get_db() resolved a strategies table with 0 rows — "
                "this is almost certainly the wrong DB (split-brain home). Set "
                "AXIOM_HOME to the live home (e.g. ~/.Axiom) and run inside the "
                "app's environment, with the app stopped.",
                file=sys.stderr,
            )
            return 2

        candidates = _candidates(conn)
        key_counts = {key: 0 for key in OVERLAY_GRIDS}
        for strategy in candidates:
            for key in strategy["_strip"]:
                key_counts[key] += 1

        print(f"Strategies scanned: {total}")
        print(f"Rows carrying overlay-grid stop/TP values: {len(candidates)}")
        for key, count in key_counts.items():
            print(f"  {key}: {count}")

        verb = "stripping" if args.apply else "would strip"
        for strategy in candidates:
            stripped = ", ".join(f"{k}={v}" for k, v in sorted(strategy["_strip"].items()))
            print(
                f"  {verb} {strategy['id']} ({strategy.get('name')}) "
                f"type={strategy.get('type')} symbol={strategy.get('symbol')} "
                f"stage={strategy.get('stage')} status={strategy.get('status')}: {stripped}"
            )

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to write (stop the app first).")
            return 0

        stamp = get_now().isoformat()
        changed = 0
        for strategy in candidates:
            params = strategy["_params"]
            for key in strategy["_strip"]:
                params.pop(key, None)
            conn.execute(
                "UPDATE strategies SET params = ?, updated_at = ? WHERE id = ?",
                (json.dumps(params), stamp, str(strategy["id"])),
            )
            changed += 1
        print(f"\nStripped overlay-grid stop/TP keys from {changed} strategies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
