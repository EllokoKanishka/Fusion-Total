from __future__ import annotations

import builtins

import re
import math
import threading
from collections.abc import Callable

from fusion_reader_v2.notes import ReaderNotesStore
from fusion_reader_v2.reader import ReaderSession


LABORATORY_NOTES_DOC_ID = "__laboratory__"
LABORATORY_NOTES_TITLE = "Laboratorio"


class NotesService:
    """Coordinates document and laboratory notes around the active reader."""

    def __init__(
        self,
        *,
        session: ReaderSession,
        notes: ReaderNotesStore,
        dialogue_history: builtins.list[dict],
        dialogue_lock: threading.Lock,
        chat_history: builtins.list[dict],
        chat_lock: threading.Lock,
        looks_like_note_request: Callable[[str], bool],
    ) -> None:
        self.session = session
        self.notes = notes
        self.dialogue_history = dialogue_history
        self.dialogue_lock = dialogue_lock
        self.chat_history = chat_history
        self.chat_lock = chat_lock
        self.looks_like_note_request = looks_like_note_request

    def _with_warnings(self, result: dict, doc_id: str) -> dict:
        warnings = self.notes.warning_codes(doc_id)
        return {**result, "warnings": warnings} if warnings else result

    def summary(self) -> dict:
        status = self.session.status()
        doc_id = str(status.get("doc_id") or "")
        if not doc_id:
            return {"ok": True, "count": 0, "current_count": 0}
        notes = self.notes.list(doc_id)
        current_index = max(0, int(status.get("current") or 1) - 1)
        current_count = sum(1 for note in notes if int(note.get("chunk_index") or 0) == current_index)
        return self._with_warnings({"ok": True, "count": len(notes), "current_count": current_count}, doc_id)

    def list(self, doc_id: str = "", chunk_index: int | None = None, current_only: bool = False) -> dict:
        status = self.session.status()
        selected_doc = str(doc_id or status.get("doc_id") or "")
        if not selected_doc:
            return {"ok": True, "doc_id": "", "items": []}
        if current_only:
            chunk_index = max(0, int(status.get("current") or 1) - 1)
        return self._with_warnings(
            {"ok": True, "doc_id": selected_doc, "items": self.notes.list(selected_doc, chunk_index=chunk_index)},
            selected_doc,
        )

    def create(self, text: str, chunk_index: int | None = None) -> dict:
        document = self.session.document
        if not document:
            return {"ok": False, "error": "no_document_loaded"}
        selected_index = self.session.cursor if chunk_index is None else int(chunk_index)
        if selected_index < 0 or selected_index >= len(document.chunks):
            return {"ok": False, "error": "chunk_out_of_bounds"}
        try:
            note = self.notes.add(
                document.doc_id,
                document.title,
                selected_index,
                text,
                quote=document.chunks[selected_index],
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return self._with_warnings(
            {"ok": True, "note": note, "items": self.notes.list(document.doc_id)}, document.doc_id
        )

    def create_laboratory(self, text: str, *, history: builtins.list[dict] | None = None) -> dict:
        clean_text = self.resolve_laboratory_text(text, history=history)
        if not clean_text:
            return {"ok": False, "error": "empty_note"}
        try:
            note = self.notes.add(
                LABORATORY_NOTES_DOC_ID,
                LABORATORY_NOTES_TITLE,
                0,
                clean_text,
                quote=self.recent_laboratory_quote(history=history),
                source_kind="laboratory",
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return self._with_warnings(
            {"ok": True, "note": note, "items": self.notes.list(LABORATORY_NOTES_DOC_ID)}, LABORATORY_NOTES_DOC_ID
        )

    def resolve_chunk_index(self, chunk_index: int | None = None) -> int | None:
        document = self.session.document
        if document is None or chunk_index is None:
            return None
        try:
            selected = int(chunk_index)
        except (TypeError, ValueError):
            return None
        return selected if 0 <= selected < len(document.chunks) else None

    def update(self, note_id: str, text: str, doc_id: str = "") -> dict:
        selected_doc = str(doc_id or self.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            note = self.notes.update(selected_doc, str(note_id or ""), text)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return self._with_warnings({"ok": True, "note": note, "items": self.notes.list(selected_doc)}, selected_doc)

    def rename(self, note_id: str, label: str, doc_id: str = "") -> dict:
        selected_doc = str(doc_id or self.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            note = self.notes.update_label(selected_doc, str(note_id or ""), label)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return self._with_warnings({"ok": True, "note": note, "items": self.notes.list(selected_doc)}, selected_doc)

    def delete(self, note_id: str, doc_id: str = "") -> dict:
        selected_doc = str(doc_id or self.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            out = self.notes.delete(selected_doc, str(note_id or ""))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return self._with_warnings({**out, "items": self.notes.list(selected_doc)}, selected_doc)

    def reference(self, note: dict) -> str:
        kind = str(note.get("source_kind") or "document").strip().lower()
        if kind == "laboratory":
            return f"L{int(note.get('anchor_number') or 1)}"
        return f"B{int(note.get('chunk_number') or note.get('anchor_number') or 1)}"

    def saved_answer(self, note: dict, spoken: bool = False) -> str:
        ref = self.reference(note)
        if str(note.get("source_kind") or "").strip().lower() == "laboratory":
            return f"Listo, guardé esa nota como {ref}." if spoken else f"Nota guardada como {ref}."
        block = note.get("chunk_number") or 1
        return f"Listo, guardé esa nota en el bloque {block}." if spoken else f"Nota guardada en el bloque {block}."

    def recent_laboratory_quote(self, *, history: builtins.list[dict] | None = None) -> str:
        history = (self._recent_laboratory_history() if history is None else history)[-4:]
        lines: builtins.list[str] = []
        for item in history:
            role = str(item.get("role") or "").strip().lower()
            content = " ".join(str(item.get("content") or "").split())
            if not content:
                continue
            prefix = "Vos" if role == "user" else "Laboratorio" if role == "assistant" else "Sistema"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines[:4]).strip()

    def _recent_laboratory_history(self) -> builtins.list[dict]:
        with self.dialogue_lock:
            dialogue_history = list(self.dialogue_history)
        with self.chat_lock:
            chat_history = list(self.chat_history)

        def latest(history: builtins.list[dict]) -> float | None:
            timestamps = []
            for item in history:
                content = str(item.get("content") or "").strip()
                if not content or self.looks_like_note_request(content):
                    continue
                try:
                    raw_stamp = item.get("ts")
                    if raw_stamp is None:
                        continue
                    stamp = float(raw_stamp)
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(stamp):
                    timestamps.append(stamp)
            return max(timestamps) if timestamps else None

        dialogue_ts, chat_ts = latest(dialogue_history), latest(chat_history)
        if chat_ts is not None and (dialogue_ts is None or chat_ts > dialogue_ts):
            return chat_history
        # Untimed legacy histories and ties retain the previous dialogue-first rule.
        return dialogue_history or chat_history

    def resolve_laboratory_text(self, text: str, *, history: builtins.list[dict] | None = None) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        if self.is_generic_laboratory_text(clean):
            target = self.recent_laboratory_target(history=history)
            return target or (clean if history is None else "")
        return clean

    def recent_laboratory_target(self, *, history: builtins.list[dict] | None = None) -> str:
        if history is None:
            history = self._recent_laboratory_history()
        for preferred_role in ("assistant", "user"):
            for item in reversed(history):
                role = str(item.get("role") or "").strip().lower()
                content = " ".join(str(item.get("content") or "").split()).strip()
                if role == preferred_role and content and not self.looks_like_note_request(content):
                    return content
        return ""

    @staticmethod
    def is_generic_laboratory_text(text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean or len(clean) <= 2:
            return True
        return bool(
            re.fullmatch(
                r"(?:d|de|del|eso|esto|eso\s+mismo|esto\s+mismo|todo\s+eso|todo\s+esto|lo\s+anterior|la\s+anterior|esa\s+frase|esta\s+frase|esa\s+idea|esta\s+idea|lo\s+que\s+acabo\s+de\s+decir|esto\s+que\s+acabo\s+de\s+decir|eso\s+que\s+acabo\s+de\s+decir|lo\s+que\s+acab(?:a|á)s?\s+de\s+decir|esto\s+que\s+acab(?:a|á)s?\s+de\s+decir|eso\s+que\s+acab(?:a|á)s?\s+de\s+decir|lo\s+[úu]ltimo\s+que\s+dijiste)",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def should_create_laboratory(self, text: str, *, history: builtins.list[dict] | None = None) -> bool:
        if self.session.document is None:
            return True
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean or not self.looks_like_recent_speech_reference(clean):
            return False
        if history is not None:
            return True
        with self.dialogue_lock:
            if self.dialogue_history:
                return True
        with self.chat_lock:
            return bool(self.chat_history)

    def should_route_generic_to_laboratory(
        self, text: str, note_text: str, *, history: builtins.list[dict] | None = None
    ) -> bool:
        if self.session.document is None:
            return True
        if not self.is_generic_pointer(note_text):
            return False
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if re.search(
            r"\b(?:documento|texto|pantalla|bloque|p[aá]rrafo|cap[ií]tulo|fragmento)\b", clean, flags=re.IGNORECASE
        ):
            return False
        if history is not None:
            return bool(history)
        with self.dialogue_lock:
            if self.dialogue_history:
                return True
        with self.chat_lock:
            return bool(self.chat_history)

    @staticmethod
    def looks_like_recent_speech_reference(text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        return bool(
            clean
            and re.search(
                r"\b(?:laboratorio|chat|conversaci[oó]n|charla|saludo|mensajes?|lo\s+que\s+dijimos|lo\s+que\s+dije|lo\s+que\s+dijiste|lo\s+que\s+hablamos|nuestro\s+saludo|esta\s+charla|esta\s+conversaci[oó]n|mensaje\s+anterior|esto\s+que\s+acabo\s+de\s+decir|eso\s+que\s+acabo\s+de\s+decir|lo\s+que\s+acabo\s+de\s+decir|esto\s+que\s+acab(?:a|á)s?\s+de\s+decir|eso\s+que\s+acab(?:a|á)s?\s+de\s+decir|lo\s+que\s+acab(?:a|á)s?\s+de\s+decir|esto\s+que\s+dijiste|eso\s+que\s+dijiste|lo\s+[úu]ltimo\s+que\s+dijiste)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def looks_like_immediate_speech_reference(text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        return bool(
            clean
            and re.search(
                r"\b(?:esto\s+que\s+acabo\s+de\s+decir|eso\s+que\s+acabo\s+de\s+decir|lo\s+que\s+acabo\s+de\s+decir|esto\s+que\s+acab(?:a|á)s?\s+de\s+decir|eso\s+que\s+acab(?:a|á)s?\s+de\s+decir|lo\s+que\s+acab(?:a|á)s?\s+de\s+decir|esto\s+que\s+dijiste|eso\s+que\s+dijiste|lo\s+[úu]ltimo\s+que\s+dijiste|lo\s+que\s+dijiste|lo\s+que\s+dije)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def is_generic_pointer(text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        if len(clean) <= 2:
            return True
        return bool(
            re.fullmatch(
                r"(?:d|de|del|eso|esto|eso\s+mismo|esto\s+mismo|todo\s+eso|todo\s+esto|lo\s+anterior|la\s+anterior|esa\s+frase|esta\s+frase|esa\s+idea|esta\s+idea)",
                clean,
                flags=re.IGNORECASE,
            )
        )
