#!/usr/bin/env python3
"""Enforce global and security-critical line/branch coverage thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CRITICAL = {
    "fusion_reader_v2/services/lifecycle.py": (95.0, 90.0),
    "fusion_reader_v2/services/persistence.py": (95.0, 90.0),
    "fusion_reader_v2/domain/jobs.py": (95.0, 90.0),
    "fusion_reader_v2/config.py": (95.0, 90.0),
    "fusion_reader_v2/services/audio_export.py": (95.0, 90.0),
    "fusion_reader_v2/output_validation.py": (95.0, 90.0),
    "fusion_reader_v2/owned_subprocess.py": (95.0, 90.0),
    "fusion_reader_v2/documents.py": (95.0, 85.0),
}


def percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def evaluate(report: dict, *, min_lines: float = 90.0, min_branches: float = 80.0) -> list[str]:
    totals = report.get("totals")
    files = report.get("files")
    if not isinstance(totals, dict) or not isinstance(files, dict):
        return ["coverage JSON must contain totals and files objects"]
    line_value = percent(int(totals.get("covered_lines", 0)), int(totals.get("num_statements", 0)))
    branch_value = percent(int(totals.get("covered_branches", 0)), int(totals.get("num_branches", 0)))
    failures: list[str] = []
    if line_value < min_lines:
        failures.append(f"global lines: {line_value:.2f}% < {min_lines:.2f}%")
    if branch_value < min_branches:
        failures.append(f"global branches: {branch_value:.2f}% < {min_branches:.2f}%")
    normalized = {str(Path(name).as_posix()).removeprefix("./"): value for name, value in files.items()}
    for filename, (line_min, branch_min) in CRITICAL.items():
        item = normalized.get(filename)
        if not isinstance(item, dict) or not isinstance(item.get("summary"), dict):
            failures.append(f"{filename}: missing from coverage report")
            continue
        summary = item["summary"]
        lines = percent(int(summary.get("covered_lines", 0)), int(summary.get("num_statements", 0)))
        branches = percent(int(summary.get("covered_branches", 0)), int(summary.get("num_branches", 0)))
        if lines < line_min:
            failures.append(f"{filename}: lines {lines:.2f}% < {line_min:.2f}%")
        if branches < branch_min:
            failures.append(f"{filename}: branches {branches:.2f}% < {branch_min:.2f}%")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=Path("coverage.json"))
    parser.add_argument("--min-lines", type=float, default=90.0)
    parser.add_argument("--min-branches", type=float, default=80.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"ERROR: invalid coverage report: {type(exc).__name__}")
        return 1
    failures = evaluate(report, min_lines=args.min_lines, min_branches=args.min_branches)
    for failure in failures:
        print(f"ERROR: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
