from __future__ import annotations

from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2 import FusionReaderV2


class NotesResponder(Protocol):
    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...


def handle_notes_get(responder: NotesResponder, path: str, raw_path: str) -> bool:
    if path != "/api/notes":
        return False
    params = parse_qs(urlparse(raw_path).query)
    doc_id = str((params.get("doc_id") or [""])[0])
    current_only = str((params.get("current_only") or ["0"])[0]).lower() in {"1", "true", "yes"}
    chunk_index_raw = str((params.get("chunk_index") or [""])[0])
    chunk_index = int(chunk_index_raw) if chunk_index_raw else None
    responder._json(
        200,
        responder.app.list_notes(doc_id=doc_id, chunk_index=chunk_index, current_only=current_only),
    )
    return True


def handle_notes_post(responder: NotesResponder, path: str, payload: dict) -> bool:
    if path == "/api/notes/create":
        chunk_index = payload.get("chunk_index")
        responder._json(
            200,
            responder.app.create_note(
                str(payload.get("text") or ""),
                chunk_index=int(chunk_index) if chunk_index is not None else None,
            ),
        )
        return True
    if path == "/api/notes/update":
        responder._json(
            200,
            responder.app.update_note(
                str(payload.get("note_id") or ""),
                str(payload.get("text") or ""),
                doc_id=str(payload.get("doc_id") or ""),
            ),
        )
        return True
    if path == "/api/notes/rename":
        responder._json(
            200,
            responder.app.rename_note(
                str(payload.get("note_id") or ""),
                str(payload.get("label") or ""),
                doc_id=str(payload.get("doc_id") or ""),
            ),
        )
        return True
    if path == "/api/notes/delete":
        responder._json(
            200,
            responder.app.delete_note(
                str(payload.get("note_id") or ""),
                doc_id=str(payload.get("doc_id") or ""),
            ),
        )
        return True
    return False


__all__ = ["handle_notes_get", "handle_notes_post"]
