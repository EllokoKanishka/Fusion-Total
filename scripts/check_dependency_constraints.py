#!/usr/bin/env python3
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_name(spec: str) -> str:
    return re.split(r"[<>=!~\[; ]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def constraint_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            names.add(package_name(line))
            if "==" not in line:
                raise ValueError(f"constraint_not_pinned:{path.name}:{line}")
    return names


def check(root: Path = ROOT) -> list[str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    required = {package_name(item) for item in project["dependencies"]}
    required.update(package_name(item) for item in project["optional-dependencies"]["dev"])
    failures: list[str] = []
    for version in ("311", "312"):
        path = root / "requirements" / f"constraints-py{version}.txt"
        try:
            names = constraint_names(path)
        except (OSError, ValueError) as exc:
            failures.append(str(exc))
            continue
        missing = sorted(required - names)
        if missing:
            failures.append(f"constraints-py{version}.txt missing: {', '.join(missing)}")
    return failures


def main() -> int:
    failures = check()
    for failure in failures:
        print(f"ERROR: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
