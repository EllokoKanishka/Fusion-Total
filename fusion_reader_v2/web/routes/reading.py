from __future__ import annotations

import base64
import binascii
from typing import Protocol

from fusion_reader_v2 import FusionReaderV2, import_document_bytes
from fusion_reader_v2.config import Settings
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.routes.documents import load_imported_document, resolve_library_path


class ReadingResponder(Protocol):
    @property
    def app(self) -> FusionReaderV2: ...

    @property
    def context(self) -> WebContext: ...

    @property
    def settings(self) -> Settings: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...


def handle_reading_post(responder: ReadingResponder, path: str, payload: dict) -> bool:
    if path == "/api/load":
        role = str(payload.get("role") or "main")
        if payload.get("book_id"):
            source = resolve_library_path(responder.context, str(payload.get("book_id")))
            result = (
                responder.app.add_reference_file(source)
                if role == "reference"
                else responder.app.load_file(source, prefetch=False)
            )
            responder._json(200, result)
            return True
        if payload.get("text"):
            result = (
                responder.app.add_reference_text(
                    str(payload.get("doc_id") or "manual"),
                    str(payload.get("title") or "Manual"),
                    str(payload.get("text")),
                    source_type="manual",
                )
                if role == "reference"
                else responder.app.load_text(
                    str(payload.get("doc_id") or "manual"),
                    str(payload.get("title") or "Manual"),
                    str(payload.get("text")),
                    prefetch=False,
                    source_type="manual",
                )
            )
            responder._json(200, result)
            return True
        if payload.get("path"):
            source = resolve_library_path(responder.context, str(payload.get("path")))
            result = (
                responder.app.add_reference_file(source)
                if role == "reference"
                else responder.app.load_file(source, prefetch=False)
            )
            responder._json(200, result)
            return True
        responder._json(400, {"ok": False, "error": "missing_text_or_book_id"})
        return True
    if path == "/api/import":
        filename = str(payload.get("filename") or "documento")
        mime = str(payload.get("mime") or "")
        role = str(payload.get("role") or "main")
        raw_b64 = str(payload.get("data_b64") or "")
        if not raw_b64:
            responder._json(400, {"ok": False, "error": "missing_file_data"})
            return True
        if len(raw_b64) > (responder.settings.limits.upload_max_bytes * 4 // 3) + 8:
            raise ValueError("base64_upload_too_large")
        try:
            decoded = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid_base64") from exc
        if len(decoded) > responder.settings.limits.upload_max_bytes:
            raise ValueError("upload_too_large")
        imported = import_document_bytes(filename, decoded, mime=mime)
        responder._json(200, load_imported_document(responder.context, imported, role=role))
        return True
    if path == "/api/reference/promote":
        responder._json(
            200,
            responder.app.promote_reference_document(str(payload.get("doc_id") or ""), prefetch=False),
        )
        return True
    if path == "/api/reference/remove":
        responder._json(200, responder.app.remove_reference_document(str(payload.get("doc_id") or "")))
        return True
    if path == "/api/document/clear":
        responder._json(200, responder.app.clear_document())
        return True
    if path == "/api/read":
        result = responder.app.read_current(play=bool(payload.get("play", False)))
        responder._result(409 if result.get("stale") else 200, result)
        return True
    if path == "/api/next":
        responder._json(200, responder.app.next())
        return True
    if path == "/api/previous":
        responder._json(200, responder.app.previous())
        return True
    if path == "/api/jump":
        responder._json(200, responder.app.jump(int(payload.get("index", 1))))
        return True
    if path == "/api/prepare/start":
        responder._json(200, responder.app.prepare_document(start=str(payload.get("start") or "cursor")))
        return True
    if path == "/api/prepare/cancel":
        responder._json(200, responder.app.cancel_prepare())
        return True
    return False


__all__ = ["handle_reading_post"]
