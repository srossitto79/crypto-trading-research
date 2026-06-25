from __future__ import annotations

import argparse
import json

from axiom.soak import collect_backend_soak_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Axiom backend soak-readiness report.")
    parser.add_argument(
        "--require-exchange-connection",
        action="store_true",
        help="Require a live HyperLiquid private connectivity check.",
    )
    parser.add_argument(
        "--stale-task-minutes",
        type=int,
        default=30,
        help="Mark running tasks older than this threshold as stale in the report.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level for output.",
    )
    args = parser.parse_args()

    report = collect_backend_soak_report(
        require_exchange_connection=bool(args.require_exchange_connection),
        stale_task_minutes=max(int(args.stale_task_minutes), 1),
    )
    print(json.dumps(report, indent=max(int(args.indent), 0), sort_keys=False))

    status = str(report.get("status") or "fail").lower()
    if status == "ok":
        return 0
    if status == "warn":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
