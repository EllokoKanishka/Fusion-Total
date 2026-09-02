#!/usr/bin/env python3
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = "fusion_reader_v2/web/static/"


def discover_static_assets(root: Path = ROOT) -> set[str]:
    """Return every browser asset that must survive wheel packaging."""

    static = root / STATIC_ROOT
    if not static.is_dir():
        return set()
    return {f"{STATIC_ROOT}{path.relative_to(static).as_posix()}" for path in static.rglob("*") if path.is_file()}


REQUIRED_ASSETS = discover_static_assets()


def missing_assets(wheel: Path | str) -> list[str]:
    path = Path(wheel)
    if not path.is_file():
        return [f"wheel_missing:{path}"]
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"wheel_invalid:{path}:{type(exc).__name__}"]
    return sorted(REQUIRED_ASSETS - names)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that a built Fusion wheel contains every browser asset.")
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    missing = missing_assets(args.wheel)
    if missing:
        for item in missing:
            print(f"ERROR: {item}")
        return 1
    if not REQUIRED_ASSETS:
        print(f"ERROR: static_source_missing:{ROOT / STATIC_ROOT}")
        return 1
    print(f"wheel assets verified: {len(REQUIRED_ASSETS)} files in {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
