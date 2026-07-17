from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Callable, Generic, MutableMapping, TypeVar


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELING = "canceling"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def terminal(self) -> bool:
        return self in {self.DONE, self.CANCELLED, self.ERROR}


@dataclass
class BackgroundJob:
    job_id: str
    job_type: str
    state: JobState = JobState.QUEUED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: int = 0
    detail: str = ""
    error_code: str = ""
    error_detail: str = ""
    cancel_requested: bool = False
    output: dict = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state.terminal

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["id"] = payload.pop("job_id")
        payload["type"] = payload.pop("job_type")
        payload["state"] = str(self.state)
        payload["terminal"] = self.terminal
        return payload


JobT = TypeVar("JobT")


class JobRegistry(Generic[JobT]):
    """Thread-safe bounded registry for application-owned background jobs."""

    def __init__(
        self,
        *,
        max_items: int = 256,
        ttl_seconds: float = 6 * 60 * 60,
        is_terminal: Callable[[JobT], bool],
        updated_at: Callable[[JobT], float],
        cleanup: Callable[[JobT], None] | None = None,
        backing: MutableMapping[str, JobT] | None = None,
    ) -> None:
        if max_items < 1:
            raise ValueError("job registry max_items must be positive")
        if ttl_seconds <= 0:
            raise ValueError("job registry ttl_seconds must be positive")
        self.max_items = int(max_items)
        self.ttl_seconds = float(ttl_seconds)
        self._is_terminal = is_terminal
        self._updated_at = updated_at
        self._cleanup = cleanup
        self._items: MutableMapping[str, JobT] = backing if backing is not None else {}
        self._lock = threading.RLock()

    def add(self, job_id: str, job: JobT) -> None:
        key = str(job_id or "").strip()
        if not key:
            raise ValueError("job id is required")
        with self._lock:
            self._prune_locked(time.time())
            if key not in self._items and len(self._items) >= self.max_items:
                self._evict_oldest_terminal_locked()
            if key not in self._items and len(self._items) >= self.max_items:
                raise RuntimeError("job_registry_full")
            self._items[key] = job

    def get(self, job_id: str) -> JobT | None:
        with self._lock:
            self._prune_locked(time.time())
            return self._items.get(str(job_id or ""))

    def update(self, job_id: str, change: Callable[[JobT], None]) -> JobT | None:
        with self._lock:
            self._prune_locked(time.time())
            item = self._items.get(str(job_id or ""))
            if item is None:
                return None
            change(item)
            return item

    def remove(self, job_id: str) -> JobT | None:
        with self._lock:
            item = self._items.pop(str(job_id or ""), None)
        self._cleanup_item(item)
        return item

    def snapshot(self) -> dict[str, JobT]:
        with self._lock:
            self._prune_locked(time.time())
            return dict(self._items)

    def prune(self, *, now: float | None = None) -> int:
        with self._lock:
            return self._prune_locked(time.time() if now is None else float(now))

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(time.time())
            return len(self._items)

    def _prune_locked(self, now: float) -> int:
        stale = [
            key
            for key, item in self._items.items()
            if self._is_terminal(item) and now - self._updated_at(item) > self.ttl_seconds
        ]
        removed = [self._items.pop(key) for key in stale]
        for item in removed:
            self._cleanup_item(item)
        return len(removed)

    def _evict_oldest_terminal_locked(self) -> None:
        candidates = [(self._updated_at(item), key) for key, item in self._items.items() if self._is_terminal(item)]
        if not candidates:
            return
        _timestamp, key = min(candidates)
        self._cleanup_item(self._items.pop(key))

    def _cleanup_item(self, item: JobT | None) -> None:
        if item is not None and self._cleanup is not None:
            self._cleanup(item)
