from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from fusion_reader_v2.reader import Document

if TYPE_CHECKING:
    from fusion_reader_v2.service import FusionReaderV2


class SessionPersistenceService:
    """Builds, stores, migrates, and restores the reader session snapshot."""

    def __init__(self, owner: FusionReaderV2) -> None:
        self.owner = owner

    def persist(self, text: str | None = None, source_path: str = "", source_type: str = "") -> None:
        owner = self.owner
        if owner._session_store is None:
            return
        status = owner.session.status()
        payload = {
            "doc_id": str(status.get("doc_id") or ""),
            "title": str(status.get("title") or ""),
            "cursor": int(status.get("cursor") or 0),
            "current": int(status.get("current") or 0),
            "total": int(status.get("total") or 0),
            "updated_ts": time.time(),
            "reasoning_mode": owner.reasoning_mode,
            "laboratory_mode": owner.laboratory_mode,
            "profile": getattr(owner, "profile", "academica"),
            "veil": getattr(owner, "veil", "lucy"),
            "voice": owner.voice.voice,
            "reference_documents": [
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "title": str(item.get("title") or ""),
                    "text": str(item.get("text") or ""),
                    "source_path": str(item.get("source_path") or ""),
                    "source_type": str(item.get("source_type") or ""),
                }
                for item in owner._reference_documents.values()
            ],
        }
        selected_source_path = str(source_path or owner._main_source_path or "")
        selected_source_type = str(source_type or owner._main_source_type or "")
        if selected_source_path:
            payload["source_path"] = selected_source_path
        if selected_source_type:
            payload["source_type"] = selected_source_type
        if text is not None:
            payload["text"] = str(text)
        else:
            previous = self.read()
            for key in ("source_path", "source_type", "text"):
                if previous.get(key):
                    payload[key] = str(previous[key])
        owner._session_store.write(payload)

    def read(self) -> dict:
        store = self.owner._session_store
        return store.read() if store is not None else {}

    def restore(self) -> None:
        owner = self.owner
        raw = self.read()
        owner.reasoning_mode = str(raw.get("reasoning_mode") or owner.reasoning_mode or "thinking")
        owner.reasoning_mode = str(owner.conversation.reasoning_status(owner.reasoning_mode).get("mode") or "thinking")
        owner.laboratory_mode = "free" if str(raw.get("laboratory_mode") or "").strip().lower() == "free" else "document"
        owner.profile = str(raw.get("profile") or "academica").strip().lower()
        owner.veil = str(raw.get("veil") or "lucy").strip().lower()
        saved_voice = str(raw.get("voice") or "").strip()
        if saved_voice:
            owner.voice.voice = saved_voice
        doc_id = str(raw.get("doc_id") or "")
        title = str(raw.get("title") or "")
        owner._reference_documents = {}
        if not doc_id:
            self._restore_references(raw, main_doc_id="")
            return
        source_path = str(raw.get("source_path") or "")
        text = self._source_text(source_path) or str(raw.get("text") or "")
        if not text.strip():
            return
        owner._reset_prepare_for_new_document()
        owner.session.load(Document.from_text(doc_id, title or doc_id, text))
        owner._main_source_path = source_path
        owner._main_source_type = str(raw.get("source_type") or "")
        try:
            cursor = int(raw.get("cursor") or 0)
        except (TypeError, ValueError):
            cursor = 0
        total = len(owner.session.document.chunks) if owner.session.document else 0
        owner.session.cursor = max(0, min(cursor, max(0, total - 1)))
        self._restore_references(raw, main_doc_id=doc_id)

    def _restore_references(self, raw: dict, *, main_doc_id: str) -> None:
        owner = self.owner
        for item in raw.get("reference_documents") or []:
            if not isinstance(item, dict):
                continue
            try:
                record = owner._document_record(
                    str(item.get("doc_id") or item.get("title") or "consulta"),
                    str(item.get("title") or "Consulta"),
                    str(item.get("text") or ""),
                    source_path=str(item.get("source_path") or ""),
                    source_type=str(item.get("source_type") or ""),
                )
            except (TypeError, ValueError):
                continue
            if record["text"].strip() and record["doc_id"] != main_doc_id:
                owner._reference_documents[record["doc_id"]] = record

    @staticmethod
    def _source_text(source_path: str) -> str:
        if not source_path:
            return ""
        path = Path(source_path)
        if not path.exists() or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""
