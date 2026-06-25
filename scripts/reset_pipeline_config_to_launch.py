"""Reset the live pipeline-threshold KV to the launch-grade defaults.

During the multi-day soak the active gate store (``Axiom:pipeline_thresholds``)
was loosened well past the code defaults — negative-return gauntlet admission,
``required_tests=['monte_carlo']`` (which SKIPS walk-forward), a no-op WFA
pass-rate band ``[0.0, 1.0]``, quick-screen ``min_profit_factor 0.9`` (losers
OK), etc. This script restores the canonical launch posture defined in
``policy.DEFAULT_PIPELINE_CONFIG`` ("strict live, achievable paper") and removes
the stale legacy ``juddex:pipeline_thresholds`` key that nothing reads.

It backs up the prior values to ``.tmp/`` first so the change is reversible.

Run:  .venv/Scripts/python.exe scripts/reset_pipeline_config_to_launch.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from axiom.db import get_db, kv_get
from axiom.policy import DEFAULT_PIPELINE_CONFIG, load_pipeline_config, save_pipeline_config

ACTIVE_KEY = "axiom:pipeline_thresholds"
LEGACY_KEY = "juddex:pipeline_thresholds"


def _backup(values: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(".tmp") / f"pipeline_thresholds_backup_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(values, indent=2, default=str), encoding="utf-8")
    return out


def main() -> None:
    before_active = kv_get(ACTIVE_KEY)
    before_legacy = kv_get(LEGACY_KEY)

    backup_path = _backup({ACTIVE_KEY: before_active, LEGACY_KEY: before_legacy})
    print(f"[reset] backed up prior values -> {backup_path}")

    # Write the launch defaults straight from code (save_pipeline_config
    # normalizes + regenerates the derived aliases). This OVERWRITES every
    # relaxed soak override.
    save_pipeline_config(DEFAULT_PIPELINE_CONFIG)
    print(f"[reset] wrote launch defaults to {ACTIVE_KEY}")

    # Drop the stale legacy key (load_pipeline_config reads the Axiom: key only).
    if before_legacy is not None:
        with get_db() as conn:
            conn.execute("DELETE FROM kv WHERE key = ?", (LEGACY_KEY,))
        print(f"[reset] deleted stale legacy key {LEGACY_KEY}")
    else:
        print(f"[reset] no legacy key {LEGACY_KEY} present")

    # Verify what the gates will now read.
    after = load_pipeline_config()
    g = after.get("gauntlet", {})
    rob = after.get("robustness_thresholds", {})
    qs = after.get("quick_screen", {})
    pt = after.get("paper_trading", {})
    print("\n[verify] active gate config now:")
    print(f"  testing_mode                   = {after.get('testing_mode')}")
    print(f"  quick_screen.min_trades        = {qs.get('min_trades')}")
    print(f"  quick_screen.min_profit_factor = {qs.get('min_profit_factor')}")
    print(f"  gauntlet.min_total_return_pct  = {g.get('min_total_return_pct')}")
    print(f"  gauntlet.required_tests        = {g.get('required_tests')}")
    print(f"  gauntlet.min_robustness_score  = {g.get('min_robustness_score')}")
    print(f"  robustness.wfa_pass_rate_band  = {rob.get('wfa_pass_rate_band')}")
    print(f"  robustness.wfa_fold_pass_rate_min = {rob.get('wfa_fold_pass_rate_min')}")
    print(f"  paper_trading.min_closed_trades   = {pt.get('min_closed_trades')}")
    print(f"  paper_trading.min_profit_factor_live = {pt.get('min_profit_factor_live')}")
    print(f"  paper_trading.min_paper_sharpe       = {pt.get('min_paper_sharpe')}")
    print(f"  paper_trading.min_profit_factor_paper = {pt.get('min_profit_factor_paper')}")


if __name__ == "__main__":
    main()
