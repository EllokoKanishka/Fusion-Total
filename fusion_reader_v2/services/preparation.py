from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from fusion_reader_v2.reader import ReaderSession
from fusion_reader_v2.tts import AudioArtifact


class VoiceSelection(Protocol):
    voice: str
    language: str


class AudioCacheReader(Protocol):
    def get(self, text: str, voice: str, language: str) -> AudioArtifact | None: ...


class TTSHealth(Protocol):
    def health(self) -> dict: ...


def new_prepare_status() -> dict:
    return {
        "ok": True,
        "status": "idle",
        "doc_id": "",
        "title": "",
        "current": 0,
        "total": 0,
        "percent": 0,
        "cached": 0,
        "generated": 0,
        "failed": 0,
        "message": "Sin preparación activa.",
        "started_ts": 0.0,
        "updated_ts": 0.0,
        "done_ts": 0.0,
    }


class PreparationService:
    """Owns whole-document audio preparation state and every worker until exit."""

    def __init__(
        self,
        *,
        session: ReaderSession,
        voice: VoiceSelection,
        cache: AudioCacheReader,
        tts: TTSHealth,
        background_condition: threading.Condition,
        background_is_open_locked: Callable[[], bool],
        document_generation: Callable[[], int],
        before_registration: Callable[[], None],
        synthesize: Callable[[str, str, str], AudioArtifact],
        human_error: Callable[[str], str],
    ) -> None:
        self.session = session
        self.voice = voice
        self.cache = cache
        self.tts = tts
        self.background_condition = background_condition
        self.background_is_open_locked = background_is_open_locked
        self.document_generation = document_generation
        self.before_registration = before_registration
        self.synthesize = synthesize
        self.human_error = human_error
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._workers: dict[threading.Thread, threading.Event] = {}
        self.generation = 0
        self.state = new_prepare_status()

    def start(self, start: str = "cursor") -> dict:
        document = self.session.document
        if not document or not document.chunks:
            return {"ok": False, "error": "no_document_loaded"}
        self.before_registration()
        with self.background_condition:
            if not self.background_is_open_locked():
                return {"ok": False, "error": "service_shutting_down"}
            with self.lock:
                self._prune_workers_locked()
                if not self.background_is_open_locked():
                    return {"ok": False, "error": "service_shutting_down"}
                if self.thread and self.thread.is_alive():
                    return dict(self.state)
                cancel_event = threading.Event()
                self.cancel_event = cancel_event
                self.generation += 1
                generation = self.generation
                document_generation = self.document_generation()
                now = time.time()
                self.state = {
                    **new_prepare_status(),
                    "status": "running",
                    "doc_id": document.doc_id,
                    "document_generation": document_generation,
                    "title": document.title,
                    "total": len(document.chunks),
                    "message": "Preparando audio del documento...",
                    "started_ts": now,
                    "updated_ts": now,
                }
                thread = threading.Thread(
                    target=self._run_worker,
                    args=(document.doc_id, start, generation, document_generation, cancel_event),
                    name=f"fusion-reader-v2-prepare-{generation}",
                    daemon=False,
                )
                self.thread = thread
                self._workers[thread] = cancel_event
                thread.start()
                return dict(self.state)

    def cancel(self, message: str = "Cancelando preparación...") -> dict:
        self.cancel_event.set()
        with self.lock:
            if self.state.get("status") == "running":
                self.state["status"] = "canceling"
                self.state["message"] = message
                self.state["updated_ts"] = time.time()
            return dict(self.state)

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def active_threads(self) -> tuple[threading.Thread, ...]:
        with self.lock:
            return self._active_threads_locked()

    def reset(self) -> None:
        with self.lock:
            self._cancel_all_workers_locked()
            self.generation += 1
            self.state = new_prepare_status()
            self.cancel_event = threading.Event()
            self.thread = None
            self._prune_workers_locked()

    def begin_shutdown(self) -> threading.Thread | None:
        with self.lock:
            self._cancel_all_workers_locked()
            if self.state.get("status") == "running":
                self.state["status"] = "canceling"
                self.state["message"] = "Cancelando preparación..."
                self.state["updated_ts"] = time.time()
            workers = self._active_threads_locked()
        if not workers:
            return None
        if len(workers) == 1:
            return workers[0]
        waiter = threading.Thread(
            target=self._join_workers,
            args=(workers,),
            name="fusion-reader-v2-prepare-shutdown",
            daemon=False,
        )
        waiter.start()
        return waiter

    def _cancel_all_workers_locked(self) -> None:
        self.cancel_event.set()
        for cancel_event in self._workers.values():
            cancel_event.set()

    def _prune_workers_locked(self) -> None:
        for worker in tuple(self._workers):
            if not worker.is_alive():
                self._workers.pop(worker, None)
        if self.thread is not None and not self.thread.is_alive():
            self.thread = None

    def _active_threads_locked(self) -> tuple[threading.Thread, ...]:
        self._prune_workers_locked()
        return tuple(worker for worker in self._workers if worker.is_alive())

    @staticmethod
    def _join_workers(workers: tuple[threading.Thread, ...]) -> None:
        for worker in workers:
            worker.join()

    def _run_worker(
        self, doc_id: str, start: str, generation: int, document_generation: int, cancel_event: threading.Event
    ) -> None:
        worker = threading.current_thread()
        try:
            self._worker(doc_id, start, generation, document_generation, cancel_event)
        except Exception as exc:
            self._finish(
                "error",
                self.human_error(type(exc).__name__),
                failed=1,
                generation=generation,
            )
        finally:
            with self.lock:
                self._workers.pop(worker, None)
                if self.thread is worker:
                    self.thread = None

    def _worker(
        self, doc_id: str, start: str, generation: int, document_generation: int, cancel_event: threading.Event
    ) -> None:
        document = self.session.document
        if not document or document.doc_id != doc_id or document_generation != self.document_generation():
            self._finish("error", "El documento activo cambió antes de preparar audio.", generation=generation)
            return
        total = len(document.chunks)
        voice = self.voice.voice
        language = self.voice.language
        start_index = self.session.cursor if start != "beginning" else 0
        order = list(range(start_index, total)) + list(range(0, start_index))
        uncached = [index for index in order if not self.cache.get(document.chunks[index], voice, language)]
        if uncached:
            health = self.tts.health()
            if not bool(health.get("ok")):
                cached_now = total - len(uncached)
                self._finish(
                    "error",
                    self.human_error(str(health.get("detail") or "")),
                    current=cached_now,
                    total=total,
                    cached=cached_now,
                    generated=0,
                    failed=len(uncached),
                    generation=generation,
                )
                return
        cached = generated = failed = processed = 0
        for index in order:
            if cancel_event.is_set():
                self._finish(
                    "canceled", "Preparación cancelada.", processed, total, cached, generated, failed, generation
                )
                return
            current = self.session.document
            if not current or current.doc_id != doc_id or document_generation != self.document_generation():
                self._finish(
                    "canceled",
                    "Preparación detenida porque cambió el documento.",
                    processed,
                    total,
                    cached,
                    generated,
                    failed,
                    generation,
                )
                return
            text = current.chunks[index]
            artifact = self.cache.get(text, voice, language)
            was_cached = artifact is not None
            if artifact is None:
                artifact = self.synthesize(text, voice, language)
            if cancel_event.is_set():
                self._finish(
                    "canceled", "Preparación cancelada.", processed, total, cached, generated, failed, generation
                )
                return
            current = self.session.document
            if not current or current.doc_id != doc_id or document_generation != self.document_generation():
                self._finish(
                    "canceled",
                    "Preparación detenida porque cambió el documento.",
                    processed,
                    total,
                    cached,
                    generated,
                    failed,
                    generation,
                )
                return
            if was_cached:
                cached += 1
            elif artifact.ok:
                generated += 1
            else:
                failed += 1
            processed += 1
            self._update(processed, total, cached, generated, failed, generation)
        if failed and not generated and not cached:
            self._finish(
                "error", self.human_error("tts_prepare_failed"), processed, total, cached, generated, failed, generation
            )
        elif failed:
            self._finish(
                "done",
                f"Preparación completada con fallas: {failed} bloque(s) sin audio.",
                processed,
                total,
                cached,
                generated,
                failed,
                generation,
            )
        else:
            self._finish(
                "done", "Documento preparado para lectura.", processed, total, cached, generated, failed, generation
            )

    def _update(self, current: int, total: int, cached: int, generated: int, failed: int, generation: int) -> None:
        with self.lock:
            if generation != self.generation:
                return
            self.state.update(
                {
                    "current": current,
                    "total": total,
                    "percent": int(((cached + generated + failed) * 100) / total) if total else 0,
                    "cached": cached,
                    "generated": generated,
                    "failed": failed,
                    "message": f"Preparando bloque {cached + generated + failed} de {total}.",
                    "updated_ts": time.time(),
                }
            )

    def _finish(
        self,
        status: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
        cached: int | None = None,
        generated: int | None = None,
        failed: int | None = None,
        generation: int | None = None,
    ) -> None:
        with self.lock:
            if generation is not None and generation != self.generation:
                return
            for key, value in {
                "current": current,
                "total": total,
                "cached": cached,
                "generated": generated,
                "failed": failed,
            }.items():
                if value is not None:
                    self.state[key] = value
            total_count = int(self.state.get("total") or 0)
            done_count = sum(int(self.state.get(key) or 0) for key in ("cached", "generated", "failed"))
            self.state.update(
                {
                    "status": status,
                    "percent": int(done_count * 100 / total_count) if total_count else 0,
                    "message": message,
                    "updated_ts": time.time(),
                    "done_ts": time.time(),
                }
            )


__all__ = ["PreparationService", "new_prepare_status"]
