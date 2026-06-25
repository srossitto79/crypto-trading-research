#!/usr/bin/env python
"""Report-only AST-guard inventory of existing custom strategy modules.

Lead-2 follow-up: F3 added the AST guard to the code-INGRESS path
(intake.register_custom_strategy_file), but the ~1,300 modules already on disk
are loaded by registry.discover()/_load_custom_strategy_module, which does NOT
scan — flipping that to enforce would brick the live roster. This script tells
you which existing modules WOULD fail the guard, so the cleanup can be planned
(rewrite / quarantine) rather than imposed all at once.

Read-only: never imports or executes any strategy; uses ast.parse only.

Usage:
    python scripts/scan_custom_strategies_report.py [--dir PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from axiom.sandbox.ast_guard import scan_source  # noqa: E402


def _default_custom_dir() -> Path:
    return REPO_ROOT / "Axiom" / "strategies" / "custom"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", type=Path, default=_default_custom_dir())
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    custom_dir: Path = args.dir
    if not custom_dir.is_dir():
        print(f"custom dir not found: {custom_dir}", file=sys.stderr)
        return 2

    files = sorted(p for p in custom_dir.glob("*.py") if p.name != "__init__.py")
    failures: list[dict] = []
    scanned = 0
    for f in files:
        scanned += 1
        try:
            report = scan_source(f.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # unreadable / parse blow-up is itself a flag
            failures.append({"file": f.name, "findings": [f"scan error: {exc}"]})
            continue
        if not report.ok:
            failures.append(
                {
                    "file": f.name,
                    "findings": [
                        f"line {fi.lineno}: {fi.kind}: {fi.message}"
                        for fi in report.findings[:20]
                    ],
                }
            )

    if args.json:
        print(json.dumps({"scanned": scanned, "failing": len(failures), "modules": failures}, indent=2))
        return 0

    print(f"Scanned {scanned} custom modules in {custom_dir}")
    print(f"Would FAIL the AST guard: {len(failures)}")
    print()
    for item in failures:
        print(f"  {item['file']}")
        for finding in item["findings"]:
            print(f"      {finding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
