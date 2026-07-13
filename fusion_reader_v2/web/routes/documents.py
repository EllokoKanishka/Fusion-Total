from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fusion_reader_v2.web.context import WebContext

ALLOWED_LIBRARY_SUFFIXES = {".txt", ".md"}


def library_items(context: WebContext) -> list[dict]:
    if not context.library_root.exists():
        return []
    items: list[dict] = []
    for path in sorted(context.library_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
            continue
        rel = path.relative_to(context.library_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            text = ""
        preview = " ".join(text.split())[:170]
        items.append(
            {
                "id": rel,
                "title": path.name,
                "bytes": path.stat().st_size,
                "preview": preview,
            }
        )
    return items


def resolve_library_path(context: WebContext, book_id: str) -> Path:
    raw = unquote(str(book_id or "")).strip()
    rel = Path(raw)
    if not raw or rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError("invalid_book_id")
    if rel.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
        raise ValueError("unsupported_book_type")
    library_root = context.library_root.resolve()
    if context.library_root.exists():
        for candidate in context.library_root.rglob("*"):
            if candidate.relative_to(context.library_root).as_posix() != raw:
                continue
            path = candidate.resolve()
            if path != library_root and library_root not in path.parents:
                raise ValueError("book_outside_library")
            if path.is_file():
                return path
            break
    raise FileNotFoundError("book_not_found")


def load_imported_document(context: WebContext, imported, role: str = "main") -> dict:
    context.converted_root.mkdir(parents=True, exist_ok=True)
    target = context.converted_root / f"{imported.doc_id}.txt"
    target.write_text(imported.text, encoding="utf-8")
    raw_target = None
    if getattr(imported, "raw_text", ""):
        raw_target = context.converted_root / f"{imported.doc_id}.raw.txt"
        raw_target.write_text(imported.raw_text, encoding="utf-8")
    if str(role or "main") == "reference":
        out = context.app.add_reference_text(
            imported.doc_id, imported.title, imported.text, source_path=str(target), source_type=imported.source_type
        )
    else:
        out = context.app.load_text(
            imported.doc_id,
            imported.title,
            imported.text,
            prefetch=False,
            source_path=str(target),
            source_type=imported.source_type,
        )
    out["role"] = "reference" if str(role or "") == "reference" else "main"
    out["source_type"] = imported.source_type
    out["import_detail"] = imported.detail
    out["converted_text"] = str(target)
    out["raw_text"] = str(raw_target) if raw_target else ""
    out["converted_bytes"] = target.stat().st_size
    return out


__all__ = ["library_items", "load_imported_document", "resolve_library_path"]
