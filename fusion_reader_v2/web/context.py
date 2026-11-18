from __future__ import annotations

import threading
import time
import os
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path

from fusion_reader_v2 import FusionReaderV2
from fusion_reader_v2.config import Settings
from fusion_reader_v2.domain.jobs import JobRegistry
from fusion_reader_v2.pdf_to_docx import JobStatus
from fusion_reader_v2.services.media import MediaProcessingService
from fusion_reader_v2.version import __version__
from fusion_reader_v2.reading_timings import ReadingTimings
from fusion_reader_v2.web.errors import APIError


@dataclass
class WebContext:
    app: FusionReaderV2
    settings: Settings
    runtime_info: dict
    import_jobs: JobRegistry[dict] = field(init=False)
    pdf_jobs: JobRegistry[JobStatus] = field(init=False)
    pdf_downloads: JobRegistry[dict] = field(init=False)
    media: MediaProcessingService = field(init=False)
    reading_timings: ReadingTimings = field(init=False)
    _threads: set[threading.Thread] = field(default_factory=set, init=False)
    _threads_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _closed: bool = field(default=False, init=False)
    _main_import_generation: int = field(default=0, init=False)
    _next_import_generation: int = field(default=0, init=False)
    _started_monotonic: float = field(default_factory=time.monotonic, init=False)
    _dictation_model_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _dictation_model_cancel: threading.Event = field(default_factory=threading.Event, init=False)
    _dictation_model_job: dict = field(default_factory=dict, init=False)
    _dictation_model_warm_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _dictation_model_warm: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        limits = self.settings.limits
        self.reading_timings = ReadingTimings(
            root=self.settings.paths.runtime / "reading_timings",
            stt_url=self.settings.providers.stt_url,
            spawn=self.start_thread,
            timeout=self.settings.providers.stt_timeout_seconds,
        )
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
        # The OpenAI selector is deliberately scoped to reader conversations.
        # Multimedia translation remains local so choosing cloud dialogue never
        # uploads a conference, recording, or book as a side effect.
        media_chat = self.app.conversation.provider
        chat_providers = getattr(media_chat, "providers", {})
        if isinstance(chat_providers, dict):
            media_chat = chat_providers.get("local", media_chat)
        self.media = MediaProcessingService(
            reader=self.app,
            stt=self.app.stt,
            chat=media_chat,
            synthesize=self.app.synthesize_for_export,
            tts_health=self.app.tts.health,
            runtime_root=self.media_root,
            converted_root=self.converted_root,
            output_root=self.media_artifacts_root,
            spawn=self.start_thread,
            timeout_seconds=limits.media_timeout_seconds,
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            max_duration_seconds=limits.media_max_duration_seconds,
            max_input_bytes=limits.media_max_bytes,
        )

        if getattr(self.app, "resource_controller", None) is not None:
            self.media.corrector = self.app._dictation_corrector
            self.app.resource_controller.bind_stt(self.media.stt)

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

    @property
    def media_root(self) -> Path:
        return self.settings.paths.runtime / "media_jobs"

    @property
    def media_artifacts_root(self) -> Path:
        return self.settings.paths.runtime / "media_artifacts"

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
            try:
                thread.start()
            except Exception:
                self._threads.discard(thread)
                raise
            return thread

    def prepare_import(self, role: str = "main") -> dict:
        with self.app._document_lock, self._threads_lock:
            if self._closed:
                raise APIError("import_cancelled", "El lector se está cerrando.", 409)
            if role != "reference":
                self._next_import_generation += 1
            return {
                "document_generation": self.app._document_generation,
                "request_generation": self._next_import_generation if role != "reference" else None,
            }

    def reserve_import(self, role: str = "main", token: dict | None = None) -> dict:
        with self.app._document_lock:
            prepared = self.prepare_import(role) if token is None else dict(token)
            with self._threads_lock:
                if self._closed:
                    raise APIError("import_cancelled", "El lector se está cerrando.", 409)
                serial = prepared.get("request_generation")
                if serial is not None:
                    self._main_import_generation = max(self._main_import_generation, serial)
            return prepared

    @contextmanager
    def import_publication(self, token: dict | None = None, job_id: str = ""):
        # Main-document changes use the same app lock. Keep the closed/cancel
        # check, converted-file writes, load and terminal update in one section.
        # Order: app document lock -> web thread lock -> registry lock.
        with self.app._document_lock, self._threads_lock:
            job = self.import_jobs.get(job_id) if job_id else None
            if self._closed or (job_id and (job is None or job.get("cancelled"))):
                raise APIError("import_cancelled", "La importación fue cancelada.", 409)
            if token is not None and (
                token["document_generation"] != self.app._document_generation
                or (
                    token.get("request_generation") is not None
                    and token["request_generation"] != self._main_import_generation
                )
            ):
                raise APIError("import_superseded", "Otro documento o pedido sustituyó esta importación.", 409)
            yield

    def dictation_model_install_status(self) -> dict:
        with self._dictation_model_lock:
            return dict(self._dictation_model_job or {"state": "idle", "terminal": True})

    def dictation_model_warm_status(self) -> dict:
        with self._dictation_model_lock:
            status = dict(self._dictation_model_warm or {"state": "cold", "terminal": True})
        if status.get("state") == "ready":
            assistant = self.app.dictation_assistant
            provider = getattr(assistant, "providers", {}).get(getattr(assistant, "selected", "rules"))
            health = dict(provider.health() or {}) if provider is not None else {}
            if (
                float(status.get("ready_until_ts") or 0) <= time.time()
                or getattr(provider, "default_model", "") != status.get("model")
                or health.get("model_resident") is not True
            ):
                return {"state": "cold", "terminal": True, "model": status.get("model", "")}
        return status

    def warm_dictation_model(self) -> dict:
        assistant = self.app.dictation_assistant
        selected = str(getattr(assistant, "selected", "rules") or "rules")
        providers = getattr(assistant, "providers", {})
        if selected not in providers:
            return {"ok": True, "state": "not_required", "terminal": True}
        provider = providers.get(selected)
        model = str(getattr(provider, "default_model", "") or "").strip()
        preload = getattr(provider, "preload_model", None)
        if provider is None or not model or not callable(preload):
            return {
                "ok": False,
                "state": "error",
                "terminal": True,
                "model": model,
                "detail": "El proveedor local no admite precarga verificable.",
            }
        health = dict(provider.health() or {})
        if not health.get("ok") or health.get("model_present") is False:
            return {
                "ok": False,
                "state": "error",
                "terminal": True,
                "model": model,
                "detail": "El modelo local no está instalado o Ollama no responde.",
            }
        with self._dictation_model_warm_lock:
            current = self.dictation_model_warm_status()
            if (
                current.get("state") == "ready"
                and current.get("model") == model
                and health.get("model_resident") is True
            ):
                return {"ok": True, **current}
            with self._dictation_model_lock:
                self._dictation_model_warm = {
                    "ok": True,
                    "state": "loading",
                    "terminal": False,
                    "model": model,
                }
            result = dict(preload(model, keep_alive="10m") or {})
            if result.get("ok"):
                result.update(
                    {
                        "state": "ready",
                        "terminal": True,
                        "ready_until_ts": time.time() + 600,
                    }
                )
            else:
                result.update({"ok": False, "state": "error", "terminal": True})
            with self._dictation_model_lock:
                self._dictation_model_warm = result
            return dict(result)

    def _run_dictation_model_install(self, provider, model: str) -> None:
        with self._dictation_model_lock:
            self._dictation_model_job.update(
                {
                    "state": "running",
                    "terminal": False,
                    "model": model,
                    "detail": f"Descargando {model}…",
                }
            )
        result = provider.install_model(model, cancel_event=self._dictation_model_cancel)
        cancelled = self._dictation_model_cancel.is_set()
        with self._dictation_model_lock:
            if cancelled:
                self._dictation_model_job.update(
                    {
                        "ok": False,
                        "state": "cancelled",
                        "terminal": True,
                        "detail": "Instalación cancelada.",
                        "model": model,
                    }
                )
            elif result.get("ok"):
                self._dictation_model_job.update(
                    {"ok": True, "state": "done", "terminal": True, "detail": "Modelo local instalado.", "model": model}
                )
            else:
                self._dictation_model_job.update(
                    {
                        "ok": False,
                        "state": "error",
                        "terminal": True,
                        "model": model,
                        "detail": str(result.get("detail") or "No pude instalar el modelo local."),
                    }
                )

    def start_dictation_model_install(self) -> dict:
        assistant = self.app.dictation_assistant
        selected = str(getattr(assistant, "selected", "rules") or "rules")
        providers = getattr(assistant, "providers", {})
        provider = providers.get(selected)
        model = str(getattr(provider, "default_model", "") or "").strip()
        installer = getattr(provider, "install_model", None)
        if provider is None or not model or not callable(installer):
            return {
                "ok": False,
                "state": "error",
                "terminal": True,
                "model": model,
                "detail": "El proveedor local no admite instalación automática.",
            }
        health = dict(provider.health() or {})
        if health.get("ok") and health.get("model_present") is not False:
            return {
                "ok": True,
                "state": "done",
                "terminal": True,
                "model": model,
                "detail": "El modelo local ya está instalado.",
            }
        with self._dictation_model_lock:
            if str(self._dictation_model_job.get("state") or "") in {"queued", "running"}:
                # Installation state and cancellation are intentionally shared,
                # so only one owned Ollama pull may run at a time. Returning the
                # active job also keeps another tab or a provider switch from
                # replacing the event that shutdown must signal.
                return dict(self._dictation_model_job)
            self._dictation_model_cancel = threading.Event()
            self._dictation_model_job = {
                "ok": True,
                "state": "queued",
                "terminal": False,
                "model": model,
                "detail": "Preparando la descarga del modelo local…",
            }
        try:
            self.start_thread(
                target=self._run_dictation_model_install,
                args=(provider, model),
                name="fusion-reader-v2-dictation-model-install",
            )
        except Exception as exc:
            with self._dictation_model_lock:
                self._dictation_model_job.update(
                    {"ok": False, "state": "error", "terminal": True, "detail": str(exc), "model": model}
                )
        return self.dictation_model_install_status()

    def request_shutdown_jobs(self) -> dict:
        with self._threads_lock:
            self._closed = True
        self._dictation_model_cancel.set()
        for pdf_job in self.pdf_jobs.snapshot().values():
            if pdf_job.state not in {"done", "cancelled", "error"}:
                pdf_job.cancelled = True
        for import_job in self.import_jobs.snapshot().values():
            if str(import_job.get("status") or "") not in {"done", "cancelled", "error"}:
                import_job["cancelled"] = True
        errors = []
        for name, service in (("media", self.media), ("reading_timings", self.reading_timings)):
            try:
                service.request_shutdown()
            except Exception as exc:
                errors.append(f"{name}:{type(exc).__name__}")
        return {"ok": not errors, "errors": errors}

    def shutdown_jobs(self, timeout: float = 10.0) -> dict:
        request = self.request_shutdown_jobs()
        with self._threads_lock:
            threads = list(self._threads)

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
            "ok": not alive and request.get("ok", True),
            "errors": request.get("errors", []),
            "state": "closed" if not alive and request.get("ok", True) else "timeout",
            "alive_threads": alive,
        }

    def status(self) -> dict:
        status = self.app.status()
        controller = getattr(self.app, "resource_controller", None)
        if controller is not None:
            status["resources"] = controller.status(self.app, self)
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
                "cache": self.app.cache.summary(),
                "jobs": {
                    "imports": len(self.import_jobs),
                    "pdf_to_docx": len(self.pdf_jobs),
                    "pdf_downloads": len(self.pdf_downloads),
                    "audio_export": status.get("audio_export", {}),
                    "media": self.media.overview(),
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
