from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import unquote

from fusion_reader_v2.documents import converted_document_id
from fusion_reader_v2.web.context import WebContext

ALLOWED_LIBRARY_SUFFIXES = {".txt", ".md"}


def library_items(context: WebContext) -> list[dict]:
    if not context.library_root.exists():
        return []
    items: list[dict] = []
    root = context.library_root.resolve()
    for path in sorted(context.library_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
            continue
        rel = path.relative_to(context.library_root).as_posix()
        try:
            if not path.resolve().is_relative_to(root):
                continue
            with path.open("rb") as handle:
                text = handle.read(2048).decode("utf-8", errors="replace")
            size = path.stat().st_size
        except OSError:
            continue
        preview = " ".join(text.split())[:170]
        items.append(
            {
                "id": rel,
                "title": path.name,
                "bytes": size,
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


def load_imported_document(
    context: WebContext, imported, role: str = "main", *, import_token: dict | None = None, job_id: str = ""
) -> dict:
    with context.import_publication(import_token, job_id):
        return _load_imported_document(context, imported, role)


def _load_imported_document(context: WebContext, imported, role: str) -> dict:
    context.converted_root.mkdir(parents=True, exist_ok=True)
    doc_id = converted_document_id(
        imported.doc_id, imported.title, imported.source_type, imported.text, getattr(imported, "raw_text", "")
    )
    target = context.converted_root / f"{doc_id}.txt"
    raw_target = None
    created: list[Path] = []

    def save_if_new(path: Path, text: str) -> None:
        if not path.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                created.append(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    try:
        save_if_new(target, imported.text)
        if getattr(imported, "raw_text", ""):
            raw_target = context.converted_root / f"{doc_id}.raw.txt"
            save_if_new(raw_target, imported.raw_text)
        if str(role or "main") == "reference":
            out = context.app.add_reference_text(
                doc_id, imported.title, imported.text, source_path=str(target), source_type=imported.source_type
            )
        else:
            out = context.app.load_text(
                doc_id,
                imported.title,
                imported.text,
                prefetch=False,
                source_path=str(target),
                source_type=imported.source_type,
            )
        if out.get("ok") is False:
            for path in created:
                path.unlink(missing_ok=True)
            return out
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    out["role"] = "reference" if str(role or "") == "reference" else "main"
    out["source_type"] = imported.source_type
    out["import_detail"] = imported.detail
    out["converted_text"] = str(target)
    out["raw_text"] = str(raw_target) if raw_target else ""
    out["converted_bytes"] = target.stat().st_size
    return out


__all__ = ["library_items", "load_imported_document", "resolve_library_path"]
