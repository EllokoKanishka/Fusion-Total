from __future__ import annotations

import re
import hashlib
import json
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from collections import deque
from pathlib import Path
from typing import List

from .services.persistence import AtomicJSONStore, PersistenceWarning


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
        self._warnings: deque[tuple[str, PersistenceWarning]] = deque(maxlen=64)
        self._blocked: set[Path] = set()
        self._legacy_review: set[str] = set()
        self._recovered_note_sources: dict[str, set[str]] = {}
        self._confirmed_note_copies: dict[str, set[str]] = {}

    @property
    def warnings(self) -> tuple[PersistenceWarning, ...]:
        with self._lock:
            return tuple(warning for _doc_id, warning in self._warnings)

    def warning_codes(self, doc_id: str) -> list[str]:
        with self._lock:
            return list(dict.fromkeys(warning.code for owner, warning in self._warnings if owner == str(doc_id or "")))

    def _warn(self, doc_id: str, code: str, path: Path) -> None:
        item = (str(doc_id or ""), PersistenceWarning(code, "Notes preserved for review", str(path)))
        if item not in self._warnings:
            self._warnings.append(item)

    def list(self, doc_id: str, chunk_index: int | None = None) -> list[dict]:
        with self._lock:
            notes = self._read_notes(doc_id)
            pending = self._pending_note_copies(doc_id)
            notes = [note for note in notes if note.note_id not in pending]
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

    def has_saved_notes(self, doc_id: str) -> bool:
        return self._path(doc_id).is_file() or (self.root / f"{safe_doc_id(doc_id)}.json").is_file()

    def _pending_note_copies(self, doc_id: str) -> set[str]:
        confirmed = self._confirmed_note_copies.get(doc_id, set())
        return {
            note.note_id
            for source in self._recovered_note_sources.get(doc_id, ())
            if source != doc_id
            for note in self._read_notes(source)
            if note.note_id not in confirmed
        }

    def legacy_notes(self, legacy_id: str, doc_id: str, title: str, chunks: List[str]) -> List[dict]:
        with self._lock:
            current = {note.note_id: note for note in self._read_notes(doc_id)}
            pending = self._pending_note_copies(doc_id)
            candidates = [
                current.get(note.note_id, note)
                for note in self._read_notes(legacy_id)
                if note.source_kind == "document"
                and note.title == title
                and (note.note_id not in current or note.note_id in pending)
            ]
            return [
                {**note.to_dict(), "can_copy": self._legacy_anchor(note, chunks) is not None} for note in candidates
            ]

    @staticmethod
    def _legacy_anchor(note: ReaderNote, chunks: List[str]) -> int | None:
        if not note.quote:
            return None
        if note.chunk_index < len(chunks) and note.quote == chunks[note.chunk_index].strip():
            return note.chunk_index
        matches = [index for index, chunk in enumerate(chunks) if chunk.strip() == note.quote]
        return matches[0] if len(matches) == 1 else None

    def copy_legacy_note(self, legacy_id: str, doc_id: str, title: str, chunks: List[str], note_id: str) -> None:
        with self._lock:
            notes = self._read_notes(doc_id)
            existing = next((note for note in notes if note.note_id == note_id), None)
            pending = self._pending_note_copies(doc_id)
            if existing and note_id not in pending:
                return
            original = next(
                (
                    note
                    for note in self._read_notes(legacy_id)
                    if note.note_id == note_id and note.source_kind == "document" and note.title == title
                ),
                None,
            )
            source = (existing or original) if original else None
            anchor = self._legacy_anchor(source, chunks) if source else None
            if source is None or anchor is None:
                raise ValueError("legacy_note_anchor_unavailable")
            verified = replace(
                source,
                doc_id=doc_id,
                chunk_index=anchor,
                chunk_number=anchor + 1,
                anchor_number=anchor + 1,
            )
            updated = [note for note in notes if note.note_id != note_id]
            confirmed = self._confirmed_note_copies.get(doc_id, set())
            self._write_notes(doc_id, updated + [verified], confirmed | {note_id})

    def _path(self, doc_id: str) -> Path:
        digest = hashlib.sha256(str(doc_id or "").encode("utf-8")).hexdigest()
        return self.root / "by-id" / f"{digest}.json"

    def _store(self, doc_id: str) -> AtomicJSONStore:
        return AtomicJSONStore(self._path(doc_id), schema_version=1, max_bytes=8 * 1024 * 1024)

    def _read_notes(self, doc_id: str) -> List[ReaderNote]:
        selected = str(doc_id or "")
        self._recovered_note_sources.pop(selected, None)
        self._confirmed_note_copies.pop(selected, None)
        canonical = self._path(selected)
        legacy = self.root / f"{safe_doc_id(selected)}.json"
        path = canonical if canonical.exists() else legacy
        is_legacy = path != canonical
        if not path.exists():
            return []
        self._blocked.discard(canonical)

        def preserve(code: str) -> None:
            self._warn(selected, code, path)
            if is_legacy:
                self._legacy_review.add(selected)
            else:
                self._blocked.add(canonical)

        try:
            if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("notes source is not a bounded regular file")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            preserve("notes_state_requires_review")
            return []
        if isinstance(raw, list) and is_legacy:
            owner = None
            items = raw
        elif isinstance(raw, dict) and raw.get("schema_version", 0) in (0, 1):
            owner = raw.get("doc_id")
            items = raw.get("notes", [])
            if raw.get("legacy_review_required"):
                self._legacy_review.add(selected)
                self._warn(selected, "legacy_notes_require_review", legacy)
        else:
            preserve("notes_state_requires_review")
            return []
        if not is_legacy and owner != selected:
            preserve("notes_identity_mismatch")
            return []
        if not isinstance(items, list):
            preserve("notes_state_requires_review")
            return []
        if not is_legacy and isinstance(raw, dict):
            copies = raw.get("confirmed_note_copies")
            if isinstance(copies, list):
                self._confirmed_note_copies[selected] = {note_id for note_id in copies if isinstance(note_id, str)}
            sources = raw.get("recovered_from")
            if isinstance(sources, list):
                self._recovered_note_sources[selected] = {source for source in sources if isinstance(source, str)}
                if self._recovered_note_sources[selected]:
                    self._warn(selected, "legacy_note_assignment_needs_review", canonical)
        notes = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                preserve("notes_state_requires_review")
                continue
            declared_owner = item.get("doc_id") or owner
            if not isinstance(declared_owner, str) or not declared_owner:
                preserve("legacy_notes_require_review")
                continue
            if declared_owner != selected:
                if not is_legacy:
                    preserve("notes_identity_mismatch")
                continue
            try:
                for key in ("note_id", "doc_id", "title", "source_kind", "label", "text", "quote"):
                    if item.get(key) is not None and not isinstance(item[key], str):
                        raise ValueError("note text fields must be strings")
                for key in ("chunk_index", "chunk_number", "anchor_number", "created_ts", "updated_ts"):
                    value = item.get(key)
                    if value is not None:
                        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                            raise ValueError("note numeric fields must be numbers")
                        if not math.isfinite(float(value)):
                            raise ValueError("note numeric fields must be finite")
                data = {**item, "doc_id": selected}
                if not data.get("note_id"):
                    data["note_id"] = uuid.uuid5(
                        uuid.NAMESPACE_URL, selected + ":" + str(index) + ":" + json.dumps(item, sort_keys=True)
                    ).hex
                note = ReaderNote.from_dict(data)
                if not math.isfinite(note.created_ts) or not math.isfinite(note.updated_ts):
                    raise ValueError("note timestamps must be finite")
            except (TypeError, ValueError, OverflowError):
                preserve("notes_state_requires_review")
                continue
            notes.append(note)
        return notes

    def _write_notes(self, doc_id: str, notes: List[ReaderNote], confirmed: set[str] | None = None) -> None:
        if self._path(doc_id) in self._blocked:
            raise ValueError("notes_state_requires_review")
        payload: dict = {"doc_id": str(doc_id or ""), "notes": [note.to_dict() for note in notes]}
        if str(doc_id or "") in self._legacy_review:
            payload["legacy_review_required"] = True
        recovered_from = self._recovered_note_sources.get(doc_id)
        if recovered_from:
            payload["recovered_from"] = sorted(recovered_from)
        if confirmed is None:
            confirmed = self._confirmed_note_copies.get(doc_id)
        if confirmed:
            payload["confirmed_note_copies"] = sorted(confirmed)
        self._store(doc_id).write(payload)
