from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class PersistenceWarning:
    code: str
    detail: str
    preserved_path: str = ""


Migration = Callable[[dict], dict]
LegacyTransform = Callable[[object], dict]


class AtomicJSONStore:
    """Versioned JSON storage with atomic replacement and safe recovery."""

    def __init__(
        self,
        path: Path | str,
        *,
        schema_version: int = 1,
        max_bytes: int = 16 * 1024 * 1024,
        migrations: dict[int, Migration] | None = None,
    ) -> None:
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.path = Path(path)
        self.schema_version = int(schema_version)
        self.max_bytes = int(max_bytes)
        self.migrations = dict(migrations or {})
        self._lock = threading.RLock()
        self._warnings: list[PersistenceWarning] = []

    @property
    def warnings(self) -> tuple[PersistenceWarning, ...]:
        with self._lock:
            return tuple(self._warnings)

    def read(
        self,
        default: dict | Callable[[], dict] | None = None,
        *,
        legacy_transform: LegacyTransform | None = None,
    ) -> dict:
        with self._lock:
            fallback = self._default_value(default)
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                return fallback
            except OSError as exc:
                self._warn("state_stat_failed", str(exc))
                return fallback
            if stat.st_size > self.max_bytes:
                self._recover("state_too_large", f"{stat.st_size} bytes exceeds {self.max_bytes}")
                return fallback
            try:
                loaded: object = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._recover("state_invalid_json", str(exc))
                return fallback
            if legacy_transform is not None:
                try:
                    loaded = legacy_transform(loaded)
                except (TypeError, ValueError) as exc:
                    self._recover("state_invalid_shape", str(exc))
                    return fallback
            if not isinstance(loaded, dict):
                self._recover("state_invalid_shape", "root JSON value must be an object")
                return fallback
            payload = dict(loaded)
            try:
                version = int(payload.get("schema_version", 0))
            except (TypeError, ValueError):
                self._recover("state_invalid_version", "schema_version must be an integer")
                return fallback
            if version > self.schema_version:
                self._recover(
                    "state_future_version",
                    f"schema {version} is newer than supported schema {self.schema_version}",
                )
                return fallback
            if version < self.schema_version:
                migrated = self._migrate(payload, version)
                if migrated is None:
                    return fallback
                self._backup_before_migration()
                try:
                    self._write_locked(migrated)
                except OSError as exc:
                    self._warn("state_migration_write_failed", str(exc))
                payload = migrated
            return payload

    def write(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise TypeError("JSON state payload must be an object")
        with self._lock:
            self._write_locked(payload)

    def _write_locked(self, payload: dict) -> None:
        value = copy.deepcopy(payload)
        value["schema_version"] = self.schema_version
        encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise ValueError(f"state exceeds maximum size of {self.max_bytes} bytes")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self._fsync_parent()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _migrate(self, payload: dict, version: int) -> dict | None:
        current = dict(payload)
        source_version = max(0, version)
        try:
            while source_version < self.schema_version:
                migration = self.migrations.get(source_version)
                if migration is not None:
                    current = migration(dict(current))
                    if not isinstance(current, dict):
                        raise TypeError("migration must return an object")
                source_version += 1
                current["schema_version"] = source_version
        except (TypeError, ValueError, KeyError) as exc:
            self._recover("state_migration_failed", str(exc))
            return None
        return current

    def _backup_before_migration(self) -> None:
        stamp = self._stamp()
        target = self.path.with_name(f"{self.path.name}.backup.{stamp}")
        try:
            shutil.copy2(self.path, target)
        except OSError as exc:
            self._warn("state_backup_failed", str(exc))

    def _recover(self, code: str, detail: str) -> None:
        preserved = ""
        if self.path.exists():
            target = self.path.with_name(f"{self.path.name}.corrupt.{self._stamp()}")
            try:
                os.replace(self.path, target)
                preserved = str(target)
            except OSError as exc:
                detail = f"{detail}; could not preserve original: {exc}"
        self._warn(code, detail, preserved)

    def _warn(self, code: str, detail: str, preserved_path: str = "") -> None:
        self._warnings.append(PersistenceWarning(code, detail, preserved_path))

    def _fsync_parent(self) -> None:
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _default_value(default: dict | Callable[[], dict] | None) -> dict:
        value = default() if callable(default) else default
        return copy.deepcopy(value or {})

    @staticmethod
    def _stamp() -> str:
        return f"{time.strftime('%Y%m%dT%H%M%S')}.{time.time_ns() % 1_000_000_000:09d}"
