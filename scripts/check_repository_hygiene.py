#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_ROOT_FILES = {
    ".env.n8n.local.example",
    "FUSION_READER_V2_BLUEPRINT.md",
    "FUSION_READER_V2_DIALOGUE.md",
    "FUSION_READER_V2_PERFORMANCE.md",
    "FUSION_READER_V2_PERSONALITY_WORKBOOK.md",
    "task.md",
}
OBSOLETE_REQUIREMENTS = {
    "requirements/core.txt",
    "requirements/dev.txt",
    "requirements/optional.txt",
}
GENERATED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "playwright-report",
    "test-results",
}
GENERATED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak", ".orig", ".swp"}


def git_tracked_files(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def check(tracked_files: Iterable[str] | None = None, root: Path = ROOT) -> list[str]:
    tracked = sorted(set(tracked_files if tracked_files is not None else git_tracked_files(root)))
    failures: list[str] = []
    for relative in tracked:
        path = Path(relative)
        if relative.startswith("runtime/"):
            failures.append(f"tracked_runtime:{relative}")
        if relative.startswith("config/"):
            failures.append(f"active_autonomy_config:{relative}")
        if relative in FORBIDDEN_ROOT_FILES:
            failures.append(f"historical_root_file:{relative}")
        if relative in OBSOLETE_REQUIREMENTS:
            failures.append(f"duplicate_requirement_manifest:{relative}")
        if GENERATED_PARTS.intersection(path.parts) or path.suffix.lower() in GENERATED_SUFFIXES:
            failures.append(f"generated_artifact:{relative}")
    return failures


def main() -> int:
    failures = check()
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        return 1
    print("repository hygiene: tracked tree is clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
