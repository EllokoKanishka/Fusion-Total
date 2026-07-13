from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2 import FusionReaderV2
from fusion_reader_v2.config import Settings
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.downloads import (
    OutputValidationError,
    cached_audio_path,
    stream_file,
    validate_output_file,
)


class AudioResponder(Protocol):
    @property
    def app(self) -> FusionReaderV2: ...

    @property
    def context(self) -> WebContext: ...

    @property
    def settings(self) -> Settings: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _send(self, status: int, content_type: str, raw: bytes) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...


def handle_audio_get(responder: AudioResponder, path: str, raw_path: str) -> bool:
    if path in {"/api/voice/voices", "/api/voices"}:
        responder._json(200, responder.app.get_voice_catalog())
        return True
    if path == "/api/voice/metrics":
        responder._json(200, responder.app.recent_voice_metrics())
        return True
    if path == "/api/voice/metrics/summary":
        responder._json(200, responder.app.voice_metrics_summary())
        return True
    if path == "/api/voice/metrics/documents":
        responder._json(200, responder.app.voice_metrics_by_document())
        return True
    if path == "/api/voice/metrics/chunks":
        params = parse_qs(urlparse(raw_path).query)
        doc_id = str((params.get("doc_id") or [""])[0])
        limit = int((params.get("limit") or ["20"])[0])
        responder._json(200, responder.app.voice_metrics_by_chunk(doc_id=doc_id, limit=limit))
        return True
    if path == "/api/audio-export/status":
        responder._json(200, responder.app.audio_export_overview())
        return True
    if path.startswith("/api/audio-export/status/"):
        status = responder.app.audio_export_status(Path(path).name)
        responder._json(200 if status.get("ok") else 404, status)
        return True
    if path.startswith("/api/audio-export/download/"):
        item = responder.app.get_audio_export_download(Path(path).name)
        if not item.get("ok"):
            responder._json(404, item)
            return True
        try:
            audio_root = Path(getattr(responder.app, "audio_export_root", responder.settings.paths.downloads))
            wav_path = validate_output_file(str(item.get("path") or ""), audio_root, suffix=".wav")
        except OutputValidationError:
            responder._json(404, {"ok": False, "error": "audio_export_file_missing"})
            return True
        stream_file(responder, wav_path, content_type="audio/wav", filename=str(item.get("filename") or "audio.wav"))
        return True
    if path.startswith("/audio/"):
        audio_path = cached_audio_path(responder.context, path)
        if not audio_path:
            responder._json(404, {"ok": False, "error": "audio_not_found"})
            return True
        responder._send(200, "audio/wav", audio_path.read_bytes())
        return True
    return False


def handle_audio_post(responder: AudioResponder, path: str, payload: dict) -> bool:
    if path == "/api/audio-export":
        block_value = payload.get("block")
        start_value = payload.get("start")
        end_value = payload.get("end")
        responder._json(
            200,
            responder.app.start_audio_export(
                str(payload.get("mode") or ""),
                block=int(block_value) if block_value is not None else None,
                start=int(start_value) if start_value is not None else None,
                end=int(end_value) if end_value is not None else None,
            ),
        )
        return True
    if path.startswith("/api/audio-export/cancel/"):
        responder._json(200, responder.app.cancel_audio_export(Path(path).name))
        return True
    if path == "/api/voice":
        responder._json(200, responder.app.set_voice(str(payload.get("voice") or "")))
        return True
    if path == "/api/voice/test":
        responder._result(
            200,
            responder.app.test_voice(
                str(payload.get("text") or "Prueba de voz neural del lector conversacional."),
                play=bool(payload.get("play", False)),
            ),
        )
        return True
    return False


__all__ = ["handle_audio_get", "handle_audio_post"]
