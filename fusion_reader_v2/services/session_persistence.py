from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.persistence import AtomicJSONStore


class VoiceSelection(Protocol):
    voice: str


class ReasoningPolicy(Protocol):
    def reasoning_status(self, mode: str = "") -> dict: ...


@dataclass(frozen=True)
class SessionPreferences:
    reasoning_mode: str
    laboratory_mode: str
    profile: str
    veil: str
    chat_provider: str = "local"


class SessionPersistenceService:
    """Builds, stores, migrates, and restores the reader session snapshot."""

    def __init__(
        self,
        *,
        session: ReaderSession,
        store: AtomicJSONStore | None,
        voice: VoiceSelection,
        conversation: ReasoningPolicy,
        references: dict[str, dict],
        get_preferences: Callable[[], SessionPreferences],
        apply_preferences: Callable[[SessionPreferences], None],
        get_main_source: Callable[[], tuple[str, str]],
        set_main_source: Callable[[str, str], None],
        reset_preparation: Callable[[], None],
        build_document_record: Callable[..., dict],
    ) -> None:
        self.session = session
        self.store = store
        self.voice = voice
        self.conversation = conversation
        self.references = references
        self.get_preferences = get_preferences
        self.apply_preferences = apply_preferences
        self.get_main_source = get_main_source
        self.set_main_source = set_main_source
        self.reset_preparation = reset_preparation
        self.build_document_record = build_document_record

    def persist(self, text: str | None = None, source_path: str = "", source_type: str = "") -> None:
        if self.store is None:
            return
        status = self.session.status()
        preferences = self.get_preferences()
        current_source_path, current_source_type = self.get_main_source()
        selected_source_path = str(source_path or current_source_path or "")
        selected_source_type = str(source_type or current_source_type or "")
        transient_document = selected_source_type == "quick_text"
        payload = {
            "doc_id": "" if transient_document else str(status.get("doc_id") or ""),
            "title": "" if transient_document else str(status.get("title") or ""),
            "cursor": 0 if transient_document else int(status.get("cursor") or 0),
            "current": 0 if transient_document else int(status.get("current") or 0),
            "total": 0 if transient_document else int(status.get("total") or 0),
            "updated_ts": time.time(),
            "reasoning_mode": preferences.reasoning_mode,
            "laboratory_mode": preferences.laboratory_mode,
            "profile": preferences.profile,
            "veil": preferences.veil,
            "chat_provider": preferences.chat_provider,
            "voice": self.voice.voice,
            "reference_documents": [
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "title": str(item.get("title") or ""),
                    "text": str(item.get("text") or ""),
                    "source_path": str(item.get("source_path") or ""),
                    "source_type": str(item.get("source_type") or ""),
                }
                for item in self.references.values()
            ],
        }
        if not transient_document:
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
        self.store.write(payload)

    def read(self) -> dict:
        return self.store.read() if self.store is not None else {}

    def restore(self) -> None:
        raw = self.read()
        current = self.get_preferences()
        reasoning_mode = str(raw.get("reasoning_mode") or current.reasoning_mode or "thinking")
        reasoning_mode = str(self.conversation.reasoning_status(reasoning_mode).get("mode") or "thinking")
        preferences = SessionPreferences(
            reasoning_mode=reasoning_mode,
            laboratory_mode="free" if str(raw.get("laboratory_mode") or "").strip().lower() == "free" else "document",
            profile=str(raw.get("profile") or "academica").strip().lower(),
            veil=str(raw.get("veil") or "lucy").strip().lower(),
            chat_provider=str(raw.get("chat_provider") or current.chat_provider or "local").strip().lower(),
        )
        self.apply_preferences(preferences)
        saved_voice = str(raw.get("voice") or "").strip()
        if saved_voice:
            self.voice.voice = saved_voice
        doc_id = str(raw.get("doc_id") or "")
        title = str(raw.get("title") or "")
        self.references.clear()
        if not doc_id:
            self._restore_references(raw, main_doc_id="")
            return
        source_path = str(raw.get("source_path") or "")
        text = self._source_text(source_path) or str(raw.get("text") or "")
        if not text.strip():
            return
        self.reset_preparation()
        self.session.load(Document.from_text(doc_id, title or doc_id, text))
        self.set_main_source(source_path, str(raw.get("source_type") or ""))
        try:
            cursor = int(raw.get("cursor") or 0)
        except (TypeError, ValueError):
            cursor = 0
        total = len(self.session.document.chunks) if self.session.document else 0
        self.session.cursor = max(0, min(cursor, max(0, total - 1)))
        self._restore_references(raw, main_doc_id=doc_id)

    def _restore_references(self, raw: dict, *, main_doc_id: str) -> None:
        for item in raw.get("reference_documents") or []:
            if not isinstance(item, dict):
                continue
            try:
                record = self.build_document_record(
                    str(item.get("doc_id") or item.get("title") or "consulta"),
                    str(item.get("title") or "Consulta"),
                    str(item.get("text") or ""),
                    source_path=str(item.get("source_path") or ""),
                    source_type=str(item.get("source_type") or ""),
                )
            except (TypeError, ValueError):
                continue
            if record["text"].strip() and record["doc_id"] != main_doc_id:
                self.references[record["doc_id"]] = record

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


__all__ = ["SessionPersistenceService", "SessionPreferences"]
