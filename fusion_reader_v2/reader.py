from __future__ import annotations

import re
import time
import os
from dataclasses import dataclass, field


DEFAULT_CHUNK_MIN_CHARS = int(os.environ.get("FUSION_READER_CHUNK_MIN_CHARS", "1200"))
DEFAULT_CHUNK_TARGET_CHARS = int(os.environ.get("FUSION_READER_CHUNK_TARGET_CHARS", "2200"))
DEFAULT_CHUNK_MAX_CHARS = int(os.environ.get("FUSION_READER_CHUNK_MAX_CHARS", "3200"))


@dataclass
class ReadingUnit:
    text: str
    boundary: str = "paragraph"


def split_text(
    text: str,
    max_chars: int | None = None,
    min_chars: int | None = None,
    target_chars: int | None = None,
) -> list[str]:
    """Split text into page-sized reading chunks while preserving natural cuts."""
    min_chars, target_chars, max_chars = normalize_chunk_limits(
        max_chars=max_chars,
        min_chars=min_chars,
        target_chars=target_chars,
    )
    clean = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    if not clean:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", clean) if p.strip() and not is_noise_paragraph(p)]
    if not paragraphs:
        return []
    normalized = "\n\n".join(paragraphs)
    if len(normalized) <= max_chars:
        return [normalized]

    units: list[ReadingUnit] = []
    for paragraph in paragraphs:
        units.extend(split_paragraph_units(paragraph, max_chars=max_chars))
    return pack_reading_units(units, min_chars=min_chars, target_chars=target_chars, max_chars=max_chars)


def normalize_chunk_limits(
    max_chars: int | None = None,
    min_chars: int | None = None,
    target_chars: int | None = None,
) -> tuple[int, int, int]:
    max_value = max(120, int(max_chars if max_chars is not None else DEFAULT_CHUNK_MAX_CHARS))
    target_value = int(target_chars if target_chars is not None else DEFAULT_CHUNK_TARGET_CHARS)
    min_value = int(min_chars if min_chars is not None else DEFAULT_CHUNK_MIN_CHARS)
    target_value = min(target_value, max_value)
    if target_value <= 0:
        target_value = max_value
    if min_value > target_value:
        min_value = max(1, int(target_value * 0.6))
    min_value = min(min_value, target_value, max_value)
    return min_value, target_value, max_value


def is_noise_paragraph(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return compact in {"0", "O"}


def split_paragraph_units(paragraph: str, max_chars: int) -> list[ReadingUnit]:
    paragraph = str(paragraph or "").strip()
    if not paragraph:
        return []
    if len(paragraph) <= max_chars:
        return [ReadingUnit(paragraph, boundary="paragraph")]

    sentences = [s.strip() for s in re.split(r"(?<=[.!?;:])\s+", paragraph) if s.strip()]
    if len(sentences) <= 1:
        return [ReadingUnit(text, boundary="sentence") for text in split_long_sentence(paragraph, max_chars=max_chars)]

    units: list[ReadingUnit] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                units.append(ReadingUnit(current, boundary="sentence"))
                current = ""
            units.extend(
                ReadingUnit(text, boundary="sentence") for text in split_long_sentence(sentence, max_chars=max_chars)
            )
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            units.append(ReadingUnit(current, boundary="sentence"))
            current = sentence
        elif current:
            current = f"{current} {sentence}"
        else:
            current = sentence
    if current:
        units.append(ReadingUnit(current, boundary="sentence"))
    return units


def split_long_sentence(sentence: str, max_chars: int | None = None) -> list[str]:
    max_chars = max_chars or DEFAULT_CHUNK_MAX_CHARS
    words = str(sentence or "").split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            chunks.append(current)
            current = word
        elif current:
            current = f"{current} {word}"
        else:
            current = word
    if current:
        chunks.append(current)
    return chunks


def join_units(units: list[ReadingUnit]) -> str:
    out = ""
    previous_boundary = ""
    for unit in units:
        text = str(unit.text or "").strip()
        if not text:
            continue
        if not out:
            out = text
        elif previous_boundary == "sentence" and unit.boundary == "sentence":
            out = f"{out} {text}"
        else:
            out = f"{out}\n\n{text}"
        previous_boundary = unit.boundary
    return out


def should_join_short_unit(current: str, next_unit: str, min_chars: int) -> bool:
    return len(str(current or "").strip()) < int(min_chars or 0) and bool(str(next_unit or "").strip())


def pack_reading_units(units: list[ReadingUnit], min_chars: int, target_chars: int, max_chars: int) -> list[str]:
    chunks: list[list[ReadingUnit]] = []
    current_units: list[ReadingUnit] = []
    current_text = ""

    def flush() -> None:
        nonlocal current_units, current_text
        if current_units:
            chunks.append(current_units)
            current_units = []
            current_text = ""

    for unit in units:
        unit_text = str(unit.text or "").strip()
        if not unit_text:
            continue
        if not current_units:
            current_units = [unit]
            current_text = unit_text
            continue

        candidate_units = current_units + [unit]
        candidate_text = join_units(candidate_units)
        candidate_len = len(candidate_text)
        current_len = len(current_text)

        if candidate_len > max_chars:
            if should_join_short_unit(current_text, unit_text, min_chars) and len(unit_text) > max_chars:
                flush()
                current_units = [unit]
                current_text = unit_text
                continue
            flush()
            current_units = [unit]
            current_text = unit_text
            continue

        if current_len >= min_chars and unit.boundary == "paragraph":
            current_gap = abs(target_chars - current_len)
            candidate_gap = abs(target_chars - candidate_len)
            if current_len >= target_chars or current_gap <= candidate_gap:
                flush()
                current_units = [unit]
                current_text = unit_text
                continue

        current_units.append(unit)
        current_text = candidate_text

    flush()

    if len(chunks) >= 2:
        tail_text = join_units(chunks[-1])
        if len(tail_text) < min_chars and len(join_units(chunks[-2] + chunks[-1])) <= max_chars:
            chunks[-2].extend(chunks[-1])
            chunks.pop()
        elif len(tail_text) < 200 and chunks[-2]:
            moved = chunks[-2].pop()
            candidate_tail = join_units([moved] + chunks[-1])
            if len(candidate_tail) <= max_chars:
                chunks[-1].insert(0, moved)
                if not chunks[-2]:
                    chunks.pop(-2)
            else:
                chunks[-2].append(moved)

    return [text for text in (join_units(chunk) for chunk in chunks) if text]


@dataclass
class Document:
    doc_id: str
    title: str
    text: str
    chunks: list[str] = field(default_factory=list)

    @classmethod
    def from_text(cls, doc_id: str, title: str, text: str, max_chars: int | None = None) -> "Document":
        return cls(doc_id=doc_id, title=title, text=text, chunks=split_text(text, max_chars=max_chars))


@dataclass
class ReaderSession:
    document: Document | None = None
    cursor: int = 0
    state: str = "idle"
    updated_ts: float = field(default_factory=time.time)

    def load(self, document: Document) -> dict:
        self.document = document
        self.cursor = 0
        self.state = "loaded" if document.chunks else "empty"
        self.updated_ts = time.time()
        return self.status()

    def current_chunk(self) -> str:
        if not self.document or not self.document.chunks:
            return ""
        return self.document.chunks[self.cursor]

    def next_chunk(self) -> str:
        if not self.document or not self.document.chunks:
            self.state = "empty"
            return ""
        if self.cursor < len(self.document.chunks) - 1:
            self.cursor += 1
            self.state = "loaded"
        else:
            self.state = "eof"
        self.updated_ts = time.time()
        return self.current_chunk()

    def previous_chunk(self) -> str:
        if not self.document or not self.document.chunks:
            self.state = "empty"
            return ""
        self.cursor = max(0, self.cursor - 1)
        self.state = "loaded"
        self.updated_ts = time.time()
        return self.current_chunk()

    def jump(self, one_based_index: int) -> str:
        if not self.document or not self.document.chunks:
            self.state = "empty"
            return ""
        idx = int(one_based_index) - 1
        if idx < 0 or idx >= len(self.document.chunks):
            raise IndexError("chunk_out_of_bounds")
        self.cursor = idx
        self.state = "loaded"
        self.updated_ts = time.time()
        return self.current_chunk()

    def status(self) -> dict:
        total = len(self.document.chunks) if self.document else 0
        return {
            "ok": True,
            "state": self.state,
            "doc_id": self.document.doc_id if self.document else "",
            "title": self.document.title if self.document else "",
            "cursor": self.cursor,
            "current": self.cursor + 1 if total else 0,
            "total": total,
            "text": self.current_chunk(),
            "updated_ts": self.updated_ts,
        }
