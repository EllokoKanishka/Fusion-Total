from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fusion_reader_v2.reader import ReaderSession
from fusion_reader_v2.tts import AudioArtifact


class VoiceSelection(Protocol):
    voice: str
    language: str


class AudioCacheReader(Protocol):
    def get(self, text: str, voice: str, language: str) -> AudioArtifact | None: ...


class ReaderService:
    """Owns reader cursor navigation and its persistence/prefetch effects."""

    def __init__(
        self,
        session: ReaderSession,
        *,
        voice: VoiceSelection,
        cache: AudioCacheReader,
        persist: Callable[[], None],
        prefetch_current: Callable[[], None],
        prefetch_next: Callable[[], None],
        status: Callable[[], dict],
        document_generation: Callable[[], int],
        artifact_for_index: Callable[[int, int, str, str, str], AudioArtifact],
        play: Callable[[Path | None], None],
        record_metric: Callable[[str, dict, str], None],
        human_tts_error: Callable[[str], str],
        tts_gate: threading.Condition,
    ) -> None:
        self.session = session
        self.voice = voice
        self.cache = cache
        self.persist = persist
        self.prefetch_current = prefetch_current
        self.prefetch_next = prefetch_next
        self.status = status
        self.document_generation = document_generation
        self.artifact_for_index = artifact_for_index
        self.play = play
        self.record_metric = record_metric
        self.human_tts_error = human_tts_error
        self.tts_gate = tts_gate
        self.interactive_tts_pending = 0
        self.read_lock = threading.Lock()
        self.read_request_sequence = 0

    def read_current(self, play: bool = True) -> dict:
        document = self.session.document
        text = self.session.current_chunk()
        if not text:
            return {**self.session.status(), "ok": False, "error": "no_current_chunk"}
        generation = self.document_generation()
        doc_id = str(document.doc_id if document else "")
        index = self.session.cursor
        voice = self.voice.voice
        language = self.voice.language
        with self.read_lock:
            self.read_request_sequence += 1
            request_id = self.read_request_sequence
        started = time.perf_counter()
        cached_before = bool(self.cache.get(text, voice, language))
        with self.tts_gate:
            self.interactive_tts_pending += 1
            self.tts_gate.notify_all()
        try:
            artifact = self.artifact_for_index(generation, index, text, voice, language)
        finally:
            with self.tts_gate:
                self.interactive_tts_pending -= 1
                self.tts_gate.notify_all()
        ready_ms = int((time.perf_counter() - started) * 1000)
        current = self.session.document
        stale = (
            generation != self.document_generation()
            or not current
            or current.doc_id != doc_id
            or self.session.cursor != index
            or self.session.current_chunk() != text
            or self.voice.voice != voice
            or self.voice.language != language
        )
        if stale:
            return {
                **self.session.status(),
                "ok": False,
                "stale": True,
                "cancelled": True,
                "detail": "audio_identity_changed",
                "error": "Lectura cancelada porque cambió el documento, el bloque o la voz.",
                "document_generation": generation,
                "requested_doc_id": doc_id,
                "requested_chunk_index": index,
                "read_request_id": request_id,
                "ready_ms": ready_ms,
                "audio_state": "cancelled",
            }
        if play and artifact.ok:
            self.play(artifact.path)
        self.prefetch_next()
        out = {
            **self.session.status(),
            "ok": artifact.ok,
            "audio": str(artifact.path or ""),
            "cached": artifact.cached,
            "detail": artifact.detail,
            "provider": artifact.provider,
            "synthesis_ms": artifact.duration_ms,
            "ready_ms": ready_ms,
            "queue_wait_ms": max(0, ready_ms - int(artifact.duration_ms or 0)),
            "generation_ms": int(artifact.duration_ms or 0),
            "cache_hit": bool(cached_before or artifact.cached),
            "document_generation": generation,
            "requested_doc_id": doc_id,
            "requested_chunk_index": index,
            "read_request_id": request_id,
            "voice": voice,
            "language": language,
            "audio_state": "ready" if artifact.ok else "error",
            "audio_ready": bool(artifact.ok),
            "audio_cached": bool(artifact.cached),
            "stale": False,
            "cancelled": False,
        }
        if not artifact.ok:
            out["error"] = self.human_tts_error(artifact.detail)
        self.record_metric("read", out, text)
        return out

    def next(self) -> dict:
        self.session.next_chunk()
        return self._after_navigation()

    def previous(self) -> dict:
        self.session.previous_chunk()
        return self._after_navigation()

    def jump(self, one_based_index: int) -> dict:
        self.session.jump(one_based_index)
        return self._after_navigation()

    def _after_navigation(self) -> dict:
        self.persist()
        self.prefetch_current()
        return self.status()


__all__ = ["ReaderService"]
