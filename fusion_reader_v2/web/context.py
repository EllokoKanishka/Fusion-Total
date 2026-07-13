from __future__ import annotations

import threading
import time
import os
from dataclasses import dataclass, field
from pathlib import Path

from fusion_reader_v2 import FusionReaderV2
from fusion_reader_v2.config import Settings
from fusion_reader_v2.domain.jobs import JobRegistry
from fusion_reader_v2.pdf_to_docx import JobStatus
from fusion_reader_v2.version import __version__


@dataclass
class WebContext:
    app: FusionReaderV2
    settings: Settings
    runtime_info: dict
    import_jobs: JobRegistry[dict] = field(init=False)
    pdf_jobs: JobRegistry[JobStatus] = field(init=False)
    pdf_downloads: JobRegistry[dict] = field(init=False)
    _threads: set[threading.Thread] = field(default_factory=set, init=False)
    _threads_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _closed: bool = field(default=False, init=False)
    _started_monotonic: float = field(default_factory=time.monotonic, init=False)

    def __post_init__(self) -> None:
        limits = self.settings.limits
        self.import_jobs = JobRegistry(
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            is_terminal=lambda item: str(item.get("status") or "") in {"done", "cancelled", "error"},
            updated_at=lambda item: float(item.get("updated_ts") or item.get("created_ts") or 0),
        )
        self.pdf_jobs = JobRegistry(
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            is_terminal=lambda item: str(item.state or "") in {"done", "cancelled", "error"},
            updated_at=lambda item: float(getattr(item, "updated_ts", getattr(item, "created_ts", 0)) or 0),
        )
        self.pdf_downloads = JobRegistry(
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            is_terminal=lambda _item: True,
            updated_at=lambda item: float(item.get("created_ts") or 0),
        )

    @property
    def library_root(self) -> Path:
        return self.settings.paths.library

    @property
    def converted_root(self) -> Path:
        return self.settings.paths.runtime / "imported_texts"

    @property
    def upload_root(self) -> Path:
        return self.settings.paths.runtime / "upload_jobs"

    @property
    def pdf_root(self) -> Path:
        return self.settings.paths.runtime / "pdf_to_word"

    def start_thread(self, *, target, args: tuple, name: str) -> threading.Thread:
        def run_owned() -> None:
            try:
                target(*args)
            finally:
                with self._threads_lock:
                    self._threads.discard(threading.current_thread())

        with self._threads_lock:
            if self._closed:
                raise RuntimeError("web_context_closed")
            thread = threading.Thread(target=run_owned, name=name, daemon=False)
            self._threads.add(thread)
            thread.start()
            return thread

    def shutdown_jobs(self, timeout: float = 10.0) -> dict:
        with self._threads_lock:
            if self._closed and not self._threads:
                return {"ok": True, "state": "closed", "alive_threads": []}
            self._closed = True
            threads = list(self._threads)
        for pdf_job in self.pdf_jobs.snapshot().values():
            if pdf_job.state not in {"done", "cancelled", "error"}:
                pdf_job.cancelled = True
        for import_job in self.import_jobs.snapshot().values():
            if str(import_job.get("status") or "") not in {"done", "cancelled", "error"}:
                import_job["cancelled"] = True
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        with self._threads_lock:
            alive = sorted(thread.name for thread in self._threads if thread.is_alive())
            if not alive:
                self._threads.clear()
        return {
            "ok": not alive,
            "state": "closed" if not alive else "timeout",
            "alive_threads": alive,
        }

    def status(self) -> dict:
        status = self.app.status()
        services = dict(status.get("services") or {})
        degradations = [
            name
            for name in ("tts", "stt", "chat", "external_research")
            if not bool((services.get(name) or {}).get("ready", (services.get(name) or {}).get("ok")))
        ]
        persistence_warnings = [
            warning.code for warning in getattr(getattr(self.app, "_session_store", None), "warnings", ())
        ]
        status.update(
            {
                "version": __version__,
                "commit": self.runtime_info.get("commit", "unknown"),
                "pid": self.runtime_info.get("pid", os.getpid()),
                "uptime_seconds": round(time.monotonic() - self._started_monotonic, 3),
                "state_schema": 1,
                "cache": self.app.cache.inspect(),
                "jobs": {
                    "imports": len(self.import_jobs),
                    "pdf_to_docx": len(self.pdf_jobs),
                    "pdf_downloads": len(self.pdf_downloads),
                    "audio_export": status.get("audio_export", {}),
                    "prepare": status.get("prepare", {}),
                },
                "providers": services,
                "ports": {
                    "api": self.settings.ports.api,
                    "tts_gpu": self.settings.ports.tts_gpu,
                    "tts_cpu": self.settings.ports.tts_cpu,
                    "stt": self.settings.ports.stt,
                    "ollama": self.settings.ports.ollama,
                    "searxng": self.settings.ports.searxng,
                },
                "warnings": persistence_warnings,
                "degradations": degradations,
                "runtime": self.runtime_info,
            }
        )
        return status


__all__ = ["WebContext"]
