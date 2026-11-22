from __future__ import annotations

import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from fusion_reader_v2.documents import converted_document_id, import_document_bytes, safe_filename
from fusion_reader_v2.reader import chunk_selection_utf16
from fusion_reader_v2.web.errors import APIError
from fusion_reader_v2.web.downloads import unique_download_target
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


def document_edit_snapshot(context: WebContext, selection: tuple[int, int, int] | None = None) -> dict:
    with context.app._document_lock:
        document = context.app.session.document
        if not document or not document.chunks:
            raise APIError("no_document_loaded", "Cargá un documento para editarlo.", 409)
        if len(document.text) > context.settings.limits.quick_text_max_chars:
            raise APIError("document_edit_too_large", "El texto supera el límite del editor.", 413)
        text = document.text.replace("\r\n", "\n").replace("\r", "\n")
        result = {
            "ok": True,
            "doc_id": document.doc_id,
            "document_generation": context.app._document_generation,
            "text": text,
        }
        if selection is not None:
            try:
                start, end = chunk_selection_utf16(text, document.chunks, *selection)
            except ValueError as exc:
                raise APIError(
                    "reader_selection_changed", "La selección cambió. Volvé a seleccionar el texto.", 409
                ) from exc
            result.update({"selection_start": start, "selection_end": end})
        return result


def save_edited_document(context: WebContext, payload: dict) -> dict:
    from docx import Document as WordDocument

    text = payload.get("text")
    generation = payload.get("document_generation")
    doc_id = payload.get("doc_id")
    if not isinstance(text, str) or not text.strip():
        raise APIError("empty_document_edit", "La copia tiene que contener texto.")
    if not isinstance(doc_id, str) or type(generation) is not int:
        raise ValueError("invalid_document_edit")
    if len(text) > context.settings.limits.quick_text_max_chars:
        raise APIError("document_edit_too_large", "El texto supera el límite del editor.", 413)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    with context.app._document_lock:
        document = context.app.session.document
        if not document or (document.doc_id, context.app._document_generation) != (doc_id, generation):
            raise APIError("document_edit_stale", "Cambió el documento activo. Tu texto sigue en el editor.", 409)
        if text == document.text.replace("\r\n", "\n").replace("\r", "\n"):
            return {**context.app.status(), "edit_unchanged": True}
        title = document.title

    stem = re.sub(r" - editado(?:_\d+)?$", "", Path(safe_filename(title)).stem)
    stem = stem.encode("utf-8")[:160].decode("utf-8", errors="ignore").rstrip() or "Documento"
    output = BytesIO()
    word = WordDocument()
    try:
        for paragraph in text.split("\n\n"):
            word.add_paragraph(paragraph)
        word.save(output)
    except (ValueError, UnicodeError) as exc:
        raise APIError("invalid_document_edit_text", "El texto incluye caracteres que Word no admite.") from exc

    data = output.getvalue()
    with context.import_publication({"document_generation": generation}):
        target = unique_download_target(context, f"{stem} - editado.docx")
        imported = import_document_bytes(target.name, data)
        if not imported.text:
            raise APIError("empty_document_edit", "La copia tiene que contener texto.")
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".panda-edit-") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(temporary.name, target)
        try:
            result = _load_imported_document(context, imported, "main")
            if not result.get("ok", True):
                target.unlink(missing_ok=True)
                return result
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        result["edited_copy"] = {"filename": target.name, "path": str(target)}
        return result


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
