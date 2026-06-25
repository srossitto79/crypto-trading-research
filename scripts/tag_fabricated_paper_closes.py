#!/usr/bin/env python
"""Tag paper trades that were force-closed by the exchange-truth reconciler.

Lead-1 / D2: before the F4 fix, reconcile_exchange_positions and
read_open_trades force-closed local-only paper trades at a HyperLiquid testnet
mid price, fabricating PnL and leaving net_pnl_pct NULL. This marks those closed
rows with ``signal_data.invalidated_fabricated_close = true`` so the promotion
gate / decay tracker can exclude them and so the provenance is auditable.

Non-destructive and reversible: only adds flags to signal_data; never deletes a
row, never touches balances (D2 — tag, do not delete). Idempotent: re-running
skips already-tagged rows. Dry-run by default.

Stop the app before --apply (start_all.ps1 supervises the backend; writing to the
live DB while it runs can race on the WAL lock).

Usage:
    python scripts/tag_fabricated_paper_closes.py            # dry-run report
    python scripts/tag_fabricated_paper_closes.py --apply    # write tags
    python scripts/tag_fabricated_paper_closes.py --revert   # remove the tags
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from axiom.db import get_db  # noqa: E402
from axiom.sim.clock import get_now  # noqa: E402
from axiom.trade_state import is_local_only_paper_trade, parse_trade_signal_data  # noqa: E402

FABRICATED_CLOSE_REASONS = (
    "reconcile_missing_on_exchange",
    "stale_missing_on_exchange",
)
TAG = "invalidated_fabricated_close"


def _candidates(conn):
    # close_reason is NOT a column — it lives in the signal_data JSON blob
    # (written by close_trade_record). Pull all closed paper trades and filter
    # in Python.
    rows = conn.execute(
        """
        SELECT id, execution_type, net_pnl_pct, signal_data
        FROM trades
        WHERE UPPER(COALESCE(status, '')) != 'OPEN'
          AND LOWER(COALESCE(execution_type, '')) IN ('paper', 'paper_challenger')
        """
    ).fetchall()
    out = []
    for row in rows:
        trade = dict(row)
        sd = parse_trade_signal_data(trade.get("signal_data"))
        reason = str(sd.get("close_reason") or sd.get("exit_reason") or "").strip()
        if reason not in FABRICATED_CLOSE_REASONS:
            continue
        trade["_close_reason"] = reason
        out.append(trade)
    return out


def _total_trades(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="write the tags")
    g.add_argument("--revert", action="store_true", help="remove the tags")
    args = ap.parse_args()

    with get_db() as conn:
        total = _total_trades(conn)
        if total == 0:
            print(
                "Refusing to run: get_db() resolved a trades table with 0 rows — "
                "this is almost certainly the wrong DB (split-brain home). Set "
                "AXIOM_HOME to the live home (e.g. ~/.Axiom) and run inside the "
                "app's environment, with the app stopped.",
                file=sys.stderr,
            )
            return 2
        candidates = _candidates(conn)
        if args.revert:
            reverted = 0
            for trade in candidates:
                sd = parse_trade_signal_data(trade.get("signal_data"))
                if TAG not in sd:
                    continue
                sd.pop(TAG, None)
                sd.pop(f"{TAG}_at", None)
                conn.execute(
                    "UPDATE trades SET signal_data = ? WHERE id = ?",
                    (json.dumps(sd), str(trade.get("id"))),
                )
                reverted += 1
            print(f"Reverted tag on {reverted} trades.")
            return 0

        to_tag = []
        already = 0
        for trade in candidates:
            sd = parse_trade_signal_data(trade.get("signal_data"))
            if sd.get(TAG):
                already += 1
                continue
            to_tag.append(trade)

        print(f"Fabricated-close paper trades found: {len(candidates)}")
        print(f"  already tagged: {already}")
        print(f"  to tag:         {len(to_tag)}")
        # Cross-check against the live predicate (defensive).
        not_local = [t for t in to_tag if not is_local_only_paper_trade(t)]
        if not_local:
            print(f"  NOTE: {len(not_local)} carry an exchange order id — review before tagging:")
            for t in not_local[:10]:
                print(f"        {t.get('id')} ({t.get('execution_type')})")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to write (stop the app first).")
            for t in to_tag[:20]:
                print(f"  would tag {t.get('id')} close_reason={t.get('close_reason')} net_pnl_pct={t.get('net_pnl_pct')}")
            return 0

        stamp = get_now().isoformat()
        tagged = 0
        for trade in to_tag:
            sd = parse_trade_signal_data(trade.get("signal_data"))
            sd[TAG] = True
            sd[f"{TAG}_at"] = stamp
            conn.execute(
                "UPDATE trades SET signal_data = ? WHERE id = ?",
                (json.dumps(sd), str(trade.get("id"))),
            )
            tagged += 1
        print(f"\nTagged {tagged} trades with {TAG}=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
