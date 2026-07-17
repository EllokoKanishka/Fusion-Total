from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

from .services.persistence import AtomicJSONStore


NOTE_LABEL_STOPWORDS = {
    "a",
    "al",
    "como",
    "con",
    "de",
    "del",
    "bloque",
    "el",
    "en",
    "es",
    "esa",
    "ese",
    "esta",
    "este",
    "la",
    "las",
    "lo",
    "los",
    "nota",
    "notas",
    "para",
    "por",
    "que",
    "se",
    "sobre",
    "toma",
    "tomar",
    "tomá",
    "tome",
    "un",
    "una",
    "y",
}


def safe_doc_id(doc_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(doc_id or "").strip()).strip("._")
    return safe or "document"


def note_label_from_text(text: str, max_words: int = 3) -> str:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", str(text or ""))
    selected: list[str] = []
    for word in words:
        clean = word.strip()
        if not clean:
            continue
        if clean.isdigit():
            continue
        if clean.lower() in NOTE_LABEL_STOPWORDS:
            continue
        selected.append(clean)
        if len(selected) >= max_words:
            break
    if not selected:
        selected = words[:max_words]
    return " ".join(selected).strip()


@dataclass(frozen=True)
class ReaderNote:
    note_id: str
    doc_id: str
    title: str
    source_kind: str
    label: str
    label_custom: bool
    chunk_index: int
    chunk_number: int
    anchor_number: int
    text: str
    quote: str = ""
    created_ts: float = 0.0
    updated_ts: float = 0.0

    @classmethod
    def create(
        cls,
        doc_id: str,
        title: str,
        chunk_index: int,
        text: str,
        quote: str = "",
        source_kind: str = "document",
        anchor_number: int | None = None,
    ) -> "ReaderNote":
        now = time.time()
        index = max(0, int(chunk_index))
        kind = "laboratory" if str(source_kind or "").strip().lower() == "laboratory" else "document"
        number = max(1, int(anchor_number if anchor_number is not None else index + 1))
        return cls(
            note_id=uuid.uuid4().hex,
            doc_id=str(doc_id or ""),
            title=str(title or ""),
            source_kind=kind,
            label=note_label_from_text(text),
            label_custom=False,
            chunk_index=index,
            chunk_number=index + 1,
            anchor_number=number,
            text=str(text or "").strip(),
            quote=str(quote or "").strip(),
            created_ts=now,
            updated_ts=now,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ReaderNote":
        chunk_index = max(0, int(data.get("chunk_index") or 0))
        return cls(
            note_id=str(data.get("note_id") or uuid.uuid4().hex),
            doc_id=str(data.get("doc_id") or ""),
            title=str(data.get("title") or ""),
            source_kind="laboratory"
            if str(data.get("source_kind") or "").strip().lower() == "laboratory"
            else "document",
            label=str(data.get("label") or note_label_from_text(data.get("text") or "")).strip(),
            label_custom=bool(data.get("label_custom", False)),
            chunk_index=chunk_index,
            chunk_number=int(data.get("chunk_number") or chunk_index + 1),
            anchor_number=int(data.get("anchor_number") or data.get("chunk_number") or chunk_index + 1),
            text=str(data.get("text") or "").strip(),
            quote=str(data.get("quote") or "").strip(),
            created_ts=float(data.get("created_ts") or time.time()),
            updated_ts=float(data.get("updated_ts") or data.get("created_ts") or time.time()),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class ReaderNotesStore:
    def __init__(self, root: Path | str = "runtime/fusion_reader_v2/notes") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def list(self, doc_id: str, chunk_index: int | None = None) -> list[dict]:
        with self._lock:
            notes = self._read_notes(doc_id)
        if chunk_index is not None:
            wanted = int(chunk_index)
            notes = [note for note in notes if note.chunk_index == wanted]
        notes.sort(key=lambda note: (note.chunk_index, note.created_ts, note.note_id))
        return [note.to_dict() for note in notes]

    def add(
        self,
        doc_id: str,
        title: str,
        chunk_index: int,
        text: str,
        quote: str = "",
        source_kind: str = "document",
        anchor_number: int | None = None,
    ) -> dict:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("empty_note")
        with self._lock:
            notes = self._read_notes(doc_id)
            if anchor_number is None and str(source_kind or "").strip().lower() == "laboratory":
                anchor_number = max((int(item.anchor_number or 0) for item in notes), default=0) + 1
            note = ReaderNote.create(
                doc_id,
                title,
                chunk_index,
                clean_text,
                quote=quote,
                source_kind=source_kind,
                anchor_number=anchor_number,
            )
            notes.append(note)
            self._write_notes(doc_id, notes)
        return note.to_dict()

    def update(self, doc_id: str, note_id: str, text: str) -> dict:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("empty_note")
        with self._lock:
            notes = self._read_notes(doc_id)
            updated: ReaderNote | None = None
            out: list[ReaderNote] = []
            for note in notes:
                if note.note_id == note_id:
                    updated = ReaderNote(
                        **{
                            **note.to_dict(),
                            "text": clean_text,
                            "label": note.label if note.label_custom else note_label_from_text(clean_text),
                            "updated_ts": time.time(),
                        }
                    )
                    out.append(updated)
                else:
                    out.append(note)
            if not updated:
                raise KeyError("note_not_found")
            self._write_notes(doc_id, out)
        return updated.to_dict()

    def update_label(self, doc_id: str, note_id: str, label: str) -> dict:
        clean_label = str(label or "").strip()
        if not clean_label:
            raise ValueError("empty_label")
        with self._lock:
            notes = self._read_notes(doc_id)
            updated: ReaderNote | None = None
            out: list[ReaderNote] = []
            for note in notes:
                if note.note_id == note_id:
                    updated = ReaderNote(
                        **{
                            **note.to_dict(),
                            "label": note_label_from_text(clean_label, max_words=4),
                            "label_custom": True,
                            "updated_ts": time.time(),
                        }
                    )
                    out.append(updated)
                else:
                    out.append(note)
            if not updated:
                raise KeyError("note_not_found")
            self._write_notes(doc_id, out)
        return updated.to_dict()

    def delete(self, doc_id: str, note_id: str) -> dict:
        with self._lock:
            notes = self._read_notes(doc_id)
            remaining = [note for note in notes if note.note_id != note_id]
            if len(remaining) == len(notes):
                raise KeyError("note_not_found")
            self._write_notes(doc_id, remaining)
        return {"ok": True, "note_id": note_id, "deleted": True}

    def _path(self, doc_id: str) -> Path:
        return self.root / f"{safe_doc_id(doc_id)}.json"

    def _store(self, doc_id: str) -> AtomicJSONStore:
        return AtomicJSONStore(self._path(doc_id), schema_version=1, max_bytes=8 * 1024 * 1024)

    def _read_notes(self, doc_id: str) -> List[ReaderNote]:
        def normalize_legacy(raw: object) -> dict:
            if isinstance(raw, list):
                return {"doc_id": str(doc_id or ""), "notes": raw}
            if isinstance(raw, dict):
                return raw
            raise ValueError("notes state must be an object or legacy list")

        raw = self._store(doc_id).read(legacy_transform=normalize_legacy)
        items = raw.get("notes", [])
        if not isinstance(items, list):
            return []
        return [ReaderNote.from_dict(item) for item in items if isinstance(item, dict)]

    def _write_notes(self, doc_id: str, notes: List[ReaderNote]) -> None:
        payload = {"doc_id": str(doc_id or ""), "notes": [note.to_dict() for note in notes]}
        self._store(doc_id).write(payload)
