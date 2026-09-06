from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2.config import Settings
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.downloads import stream_file


class MediaResponder(Protocol):
    path: str

    @property
    def context(self) -> WebContext: ...

    @property
    def settings(self) -> Settings: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _read_multipart_file(
        self,
        *,
        field_name: str,
        max_bytes: int | None = None,
        limit_error: str = "pdf_too_large",
    ) -> tuple[str, str, Path]: ...


def handle_media_get(responder: MediaResponder, path: str) -> bool:
    if path == "/api/media/capabilities":
        params = parse_qs(urlparse(responder.path).query)

        def selected(name: str, default: bool) -> bool:
            raw = str((params.get(name) or ["1" if default else "0"])[-1]).strip().lower()
            return raw not in {"", "0", "false", "no", "off"}

        operation = str((params.get("operation") or ["transcribe"])[-1]).strip().lower()
        try:
            input_bytes = max(0, int(str((params.get("file_bytes") or ["0"])[-1])))
        except ValueError:
            input_bytes = 0
        payload = responder.context.media.capabilities(
            operation=operation,
            include_translated_pdf=selected("translated_pdf", operation == "translate"),
            include_spanish_audio=selected("spanish_audio", operation == "translate"),
            input_bytes=input_bytes,
        )
        responder._json(200 if payload.get("ok") else 503, payload)
        return True
    if path == "/api/media/status":
        responder._json(200, responder.context.media.overview())
        return True
    if path.startswith("/api/media/status/"):
        payload = responder.context.media.status(Path(path).name)
        responder._json(404 if payload.get("error") == "media_job_not_found" else 200, payload)
        return True
    if path.startswith("/api/media/download/"):
        parts = [part for part in path.split("/") if part]
        if len(parts) != 5:
            responder._json(404, {"ok": False, "error": "media_artifact_not_found"})
            return True
        job_id, kind = parts[-2], parts[-1]
        item = responder.context.media.artifact(job_id, kind)
        if not item.get("ok"):
            responder._json(404, item)
            return True
        content_types = {
            "pdf": "application/pdf",
            "translated-pdf": "application/pdf",
            "audio": "audio/wav",
        }
        stream_file(
            responder,
            Path(str(item["path"])),
            content_type=content_types.get(kind, "application/octet-stream"),
            filename=str(item["filename"]),
        )
        return True
    return False


def handle_media_post(responder: MediaResponder, path: str, payload: dict | None = None) -> bool:
    if path in {"/api/media/transcribe", "/api/media/translate"}:
        filename, mime, input_path = responder._read_multipart_file(
            field_name="file",
            max_bytes=responder.settings.limits.media_max_bytes,
            limit_error="media_too_large",
        )
        operation = "translate" if path.endswith("/translate") else "transcribe"
        params = parse_qs(urlparse(responder.path).query)

        def selected(name: str, default: bool) -> bool:
            raw = str((params.get(name) or ["1" if default else "0"])[-1]).strip().lower()
            return raw not in {"", "0", "false", "no", "off"}

        result = responder.context.media.start(
            operation=operation,
            filename=filename,
            mime=mime,
            input_path=input_path,
            voice=responder.context.app.voice.voice,
            include_original_pdf=selected("original_pdf", True),
            include_translated_pdf=selected("translated_pdf", operation == "translate"),
            include_spanish_audio=selected("spanish_audio", operation == "translate"),
            stt_initial_prompt=str((params.get("stt_prompt") or [""])[-1]),
            stt_hotwords=str((params.get("stt_hotwords") or [""])[-1]),
        )
        responder._json(200 if result.get("ok") else 409, result)
        return True
    if path.startswith("/api/media/cancel/"):
        result = responder.context.media.cancel(Path(path).name)
        responder._json(200 if result.get("ok") else 404, result)
        return True
    if path.startswith("/api/media/mount/"):
        result = responder.context.media.mount(Path(path).name)
        responder._json(200 if result.get("ok") else 409, result)
        return True
    return False


__all__ = ["handle_media_get", "handle_media_post"]
