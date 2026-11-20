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


REQUIRED_ASSETS = discover_static_assets() | {
    f"fusion_reader_v2/assets/fonts/{name}" for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "LICENSE-DejaVu.txt")
}


def wheel_issues(wheel: Path | str) -> list[str]:
    path = Path(wheel)
    if not path.is_file():
        return [f"wheel_missing:{path}"]
    try:
        with zipfile.ZipFile(path) as archive:
            names = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"wheel_invalid:{path}:{type(exc).__name__}"]
    unexpected = [
        f"unexpected_wheel_entry:{name}"
        for name in names
        if not name.split("/", 1)[0].endswith(".dist-info")
        and (not name.startswith(("fusion_reader_v2/", "scripts/")) or not (ROOT / name).is_file())
    ]
    return sorted(REQUIRED_ASSETS - names) + sorted(unexpected)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that a Fusion wheel contains its browser assets and current source files."
    )
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    issues = wheel_issues(args.wheel)
    if issues:
        for item in issues:
            print(f"ERROR: {item}")
        return 1
    if not discover_static_assets():
        print(f"ERROR: static_source_missing:{ROOT / STATIC_ROOT}")
        return 1
    print(f"wheel assets verified: {len(REQUIRED_ASSETS)} files in {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
