from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fusion_reader_v2.service import FusionReaderV2


LABORATORY_NOTES_DOC_ID = "__laboratory__"
LABORATORY_NOTES_TITLE = "Laboratorio"


class NotesService:
    """Coordinates document and laboratory notes around the active reader."""

    def __init__(self, owner: FusionReaderV2) -> None:
        self.owner = owner

    def summary(self) -> dict:
        owner = self.owner
        status = owner.session.status()
        doc_id = str(status.get("doc_id") or "")
        if not doc_id:
            return {"ok": True, "count": 0, "current_count": 0}
        notes = owner.notes.list(doc_id)
        current_index = max(0, int(status.get("current") or 1) - 1)
        current_count = sum(1 for note in notes if int(note.get("chunk_index") or 0) == current_index)
        return {"ok": True, "count": len(notes), "current_count": current_count}

    def list(self, doc_id: str = "", chunk_index: int | None = None, current_only: bool = False) -> dict:
        owner = self.owner
        status = owner.session.status()
        selected_doc = str(doc_id or status.get("doc_id") or "")
        if not selected_doc:
            return {"ok": True, "doc_id": "", "items": []}
        if current_only:
            chunk_index = max(0, int(status.get("current") or 1) - 1)
        return {"ok": True, "doc_id": selected_doc, "items": owner.notes.list(selected_doc, chunk_index=chunk_index)}

    def create(self, text: str, chunk_index: int | None = None) -> dict:
        owner = self.owner
        document = owner.session.document
        if not document:
            return {"ok": False, "error": "no_document_loaded"}
        selected_index = owner.session.cursor if chunk_index is None else int(chunk_index)
        if selected_index < 0 or selected_index >= len(document.chunks):
            return {"ok": False, "error": "chunk_out_of_bounds"}
        try:
            note = owner.notes.add(
                document.doc_id,
                document.title,
                selected_index,
                text,
                quote=document.chunks[selected_index],
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": owner.notes.list(document.doc_id)}

    def create_laboratory(self, text: str) -> dict:
        owner = self.owner
        clean_text = owner._resolve_laboratory_note_text(text)
        if not clean_text:
            return {"ok": False, "error": "empty_note"}
        try:
            note = owner.notes.add(
                LABORATORY_NOTES_DOC_ID,
                LABORATORY_NOTES_TITLE,
                0,
                clean_text,
                quote=owner._recent_laboratory_quote(),
                source_kind="laboratory",
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": owner.notes.list(LABORATORY_NOTES_DOC_ID)}

    def resolve_chunk_index(self, chunk_index: int | None = None) -> int | None:
        document = self.owner.session.document
        if document is None or chunk_index is None:
            return None
        try:
            selected = int(chunk_index)
        except (TypeError, ValueError):
            return None
        return selected if 0 <= selected < len(document.chunks) else None

    def update(self, note_id: str, text: str, doc_id: str = "") -> dict:
        owner = self.owner
        selected_doc = str(doc_id or owner.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            note = owner.notes.update(selected_doc, str(note_id or ""), text)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": owner.notes.list(selected_doc)}

    def rename(self, note_id: str, label: str, doc_id: str = "") -> dict:
        owner = self.owner
        selected_doc = str(doc_id or owner.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            note = owner.notes.update_label(selected_doc, str(note_id or ""), label)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": owner.notes.list(selected_doc)}

    def delete(self, note_id: str, doc_id: str = "") -> dict:
        owner = self.owner
        selected_doc = str(doc_id or owner.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            out = owner.notes.delete(selected_doc, str(note_id or ""))
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        return {**out, "items": owner.notes.list(selected_doc)}

    def reference(self, note: dict) -> str:
        kind = str(note.get("source_kind") or "document").strip().lower()
        if kind == "laboratory":
            return f"L{int(note.get('anchor_number') or 1)}"
        return f"B{int(note.get('chunk_number') or note.get('anchor_number') or 1)}"

    def saved_answer(self, note: dict, spoken: bool = False) -> str:
        ref = self.owner._note_reference(note)
        if str(note.get("source_kind") or "").strip().lower() == "laboratory":
            return f"Listo, guardé esa nota como {ref}." if spoken else f"Nota guardada como {ref}."
        block = note.get("chunk_number") or 1
        return f"Listo, guardé esa nota en el bloque {block}." if spoken else f"Nota guardada en el bloque {block}."

    def recent_laboratory_quote(self) -> str:
        owner = self.owner
        with owner._dialogue_lock:
            dialogue_history = list(owner._dialogue_history[-4:])
        with owner._chat_lock:
            chat_history = list(owner._chat_history[-4:])
        history = dialogue_history or chat_history
        lines: list[str] = []
        for item in history:
            role = str(item.get("role") or "").strip().lower()
            content = " ".join(str(item.get("content") or "").split())
            if not content:
                continue
            prefix = "Vos" if role == "user" else "Laboratorio" if role == "assistant" else "Sistema"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines[:4]).strip()

    def resolve_laboratory_text(self, text: str) -> str:
        owner = self.owner
        clean = str(text or "").strip()
        if not clean:
            return ""
        if owner._is_generic_laboratory_note_text(clean):
            return owner._recent_laboratory_note_target() or clean
        return clean

    def recent_laboratory_target(self) -> str:
        owner = self.owner
        with owner._dialogue_lock:
            dialogue_history = list(owner._dialogue_history)
        with owner._chat_lock:
            chat_history = list(owner._chat_history)
        for history in (dialogue_history, chat_history):
            for preferred_role in ("assistant", "user"):
                for item in reversed(history):
                    role = str(item.get("role") or "").strip().lower()
                    content = " ".join(str(item.get("content") or "").split()).strip()
                    if role == preferred_role and content and not owner._looks_like_note_request(content):
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

    def should_create_laboratory(self, text: str) -> bool:
        owner = self.owner
        if owner.session.document is None:
            return True
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean or not owner._looks_like_recent_speech_reference(clean):
            return False
        with owner._dialogue_lock:
            if owner._dialogue_history:
                return True
        with owner._chat_lock:
            return bool(owner._chat_history)

    def should_route_generic_to_laboratory(self, text: str, note_text: str) -> bool:
        owner = self.owner
        if owner.session.document is None:
            return True
        if not owner._is_generic_note_pointer(note_text):
            return False
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if re.search(
            r"\b(?:documento|texto|pantalla|bloque|p[aá]rrafo|cap[ií]tulo|fragmento)\b", clean, flags=re.IGNORECASE
        ):
            return False
        with owner._dialogue_lock:
            if owner._dialogue_history:
                return True
        with owner._chat_lock:
            return bool(owner._chat_history)

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
