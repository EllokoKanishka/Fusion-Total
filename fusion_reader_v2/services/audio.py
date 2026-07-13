from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fusion_reader_v2.metrics import VoiceMetricsStore
from fusion_reader_v2.tts import AudioArtifact, TTSProvider


class VoiceState(Protocol):
    voice: str
    language: str


class AudioService:
    """Owns voice catalog, selection, test synthesis and voice metrics."""

    def __init__(
        self,
        *,
        tts: TTSProvider,
        voice: VoiceState,
        metrics: VoiceMetricsStore,
        synthesize: Callable[[str], AudioArtifact],
        play: Callable[[Path | None], None],
        record_metric: Callable[[str, dict, str], None],
        persist: Callable[[], None],
        clear_prefetch: Callable[[], None],
        prepare_status: Callable[[], dict],
        cancel_prepare: Callable[[], dict],
        status: Callable[[], dict],
    ) -> None:
        self.tts = tts
        self.voice = voice
        self.metrics = metrics
        self.synthesize = synthesize
        self.play = play
        self.record_metric = record_metric
        self.persist = persist
        self.clear_prefetch = clear_prefetch
        self.prepare_status = prepare_status
        self.cancel_prepare = cancel_prepare
        self.status = status

    def test(self, text: str, *, play: bool) -> dict:
        started = time.perf_counter()
        artifact = self.synthesize(text)
        ready_ms = int((time.perf_counter() - started) * 1000)
        if play and artifact.ok:
            self.play(artifact.path)
        out = {
            "ok": artifact.ok,
            "audio": str(artifact.path or ""),
            "cached": artifact.cached,
            "detail": artifact.detail,
            "provider": artifact.provider,
            "synthesis_ms": artifact.duration_ms,
            "ready_ms": ready_ms,
        }
        self.record_metric("voice_test", out, text)
        return out

    def catalog(self, *, fallback_current: bool = False) -> dict:
        available = self.tts.voices()
        return {
            "ok": True,
            "voices": available if available or not fallback_current else [self.voice.voice],
            "current": self.voice.voice,
        }

    def set_voice(self, voice: str) -> dict:
        clean = str(voice or "").strip()
        if not clean:
            return {"ok": False, "error": "voice_empty"}
        catalog = self.tts.voices()
        if catalog and clean not in catalog:
            return {"ok": False, "error": "voice_not_in_catalog"}
        self.voice.voice = clean
        self.persist()
        self.clear_prefetch()
        if self.prepare_status().get("status") == "running":
            self.cancel_prepare()
        return self.status()

    def recent(self, limit: int) -> dict:
        return {"ok": True, "items": self.metrics.recent(limit=limit)}

    def summary(self, limit: int) -> dict:
        return {"ok": True, "items": self.metrics.summary(limit=limit)}

    def by_document(self, limit: int) -> dict:
        return {"ok": True, "items": self.metrics.document_summary(limit=limit)}

    def by_chunk(self, doc_id: str, limit: int) -> dict:
        return {"ok": True, "items": self.metrics.chunk_summary(doc_id=doc_id, limit=limit)}


__all__ = ["AudioService", "VoiceState"]
