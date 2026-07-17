#!/usr/bin/env python3
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

STATIC_ROOT = "fusion_reader_v2/web/static/"
REQUIRED_ASSETS = {
    f"{STATIC_ROOT}index.html",
    f"{STATIC_ROOT}styles.css",
    f"{STATIC_ROOT}app.js",
    f"{STATIC_ROOT}busy_controls.js",
    f"{STATIC_ROOT}js/api.mjs",
    f"{STATIC_ROOT}js/audio.mjs",
    f"{STATIC_ROOT}js/audio_export.mjs",
    f"{STATIC_ROOT}js/bootstrap.mjs",
    f"{STATIC_ROOT}js/busy.mjs",
    f"{STATIC_ROOT}js/dialogue.mjs",
    f"{STATIC_ROOT}js/notes.mjs",
    f"{STATIC_ROOT}js/preparation.mjs",
    f"{STATIC_ROOT}js/ui.mjs",
}


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
    print(f"wheel assets verified: {len(REQUIRED_ASSETS)} files in {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
