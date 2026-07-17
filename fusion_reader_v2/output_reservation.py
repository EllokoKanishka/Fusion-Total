from __future__ import annotations

import errno
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@dataclass
class OutputReservation:
    path: Path
    published: bool = False

    def publish(self, temporary: Path | str) -> Path:
        source = Path(temporary)
        try:
            os.replace(source, self.path)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            self._publish_cross_device(source)
        self.published = True
        _fsync_directory(self.path.parent)
        return self.path

    def _publish_cross_device(self, source: Path) -> None:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".publish",
            dir=self.path.parent,
        )
        staged = Path(name)
        descriptor_open = True
        try:
            with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
                descriptor_open = False
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
            os.replace(staged, self.path)
        except Exception:
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            staged.unlink(missing_ok=True)
            raise
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass

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
