from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OutputReservation:
    path: Path
    published: bool = False

    def publish(self, temporary: Path) -> Path:
        os.replace(temporary, self.path)
        self.published = True
        return self.path

    def cleanup(self) -> None:
        if not self.published:
            self.path.unlink(missing_ok=True)


def reserve_output_path(root: Path | str, filename: str, *, default_suffix: str) -> OutputReservation:
    directory = Path(root).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    requested = Path(filename).name
    candidate = directory / requested
    if not candidate.suffix:
        candidate = candidate.with_suffix(default_suffix)
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        path = candidate if index == 1 else directory / f"{stem}_{index}{suffix}"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return OutputReservation(path)
    raise RuntimeError("no_safe_output_slot")


__all__ = ["OutputReservation", "reserve_output_path"]
