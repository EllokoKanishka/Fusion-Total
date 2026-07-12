#!/usr/bin/env python3
"""Enforce independent line and branch coverage thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=Path("coverage.json"))
    parser.add_argument("--min-lines", type=float, default=85.0)
    parser.add_argument("--min-branches", type=float, default=80.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    totals = json.loads(args.report.read_text(encoding="utf-8"))["totals"]
    line_percent = float(totals["percent_statements_covered"])
    branch_percent = float(totals["percent_branches_covered"])
    print(f"coverage: lines={line_percent:.2f}% branches={branch_percent:.2f}%")

    failures: list[str] = []
    if line_percent < args.min_lines:
        failures.append(f"line coverage {line_percent:.2f}% is below {args.min_lines:.2f}%")
    if branch_percent < args.min_branches:
        failures.append(f"branch coverage {branch_percent:.2f}% is below {args.min_branches:.2f}%")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
