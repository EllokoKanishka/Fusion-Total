from __future__ import annotations

import time
import uuid
from pathlib import Path

from fusion_reader_v2 import import_document_path
from fusion_reader_v2.pdf_to_docx import ConversionResult
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.routes.documents import load_imported_document


def new_import_job(
    context: WebContext,
    filename: str,
    mime: str,
    upload_path: Path,
    size_bytes: int,
    role: str = "main",
) -> dict:
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    job = {
        "ok": True,
        "job_id": job_id,
        "filename": filename,
        "mime": mime,
        "status": "queued",
        "stage": "queued",
        "current": 0,
        "total": 0,
        "percent": 0,
        "message": "Documento recibido. Esperando conversión...",
        "role": "reference" if str(role or "") == "reference" else "main",
        "size_bytes": size_bytes,
        "created_ts": now,
        "updated_ts": now,
        "result": None,
        "error": "",
    }
    context.import_jobs.add(job_id, job)
    return dict(job)


def prune_import_jobs(context: WebContext) -> int:
    return context.import_jobs.prune()


def update_import_job(context: WebContext, job_id: str, **changes) -> None:
    def update(job: dict) -> None:
        job.update(changes)
        current = int(job.get("current") or 0)
        total = int(job.get("total") or 0)
        if total > 0:
            job["percent"] = max(0, min(100, int(current * 100 / total)))
        job["updated_ts"] = time.time()

    context.import_jobs.update(job_id, update)


def import_progress_for(context: WebContext, job_id: str):
    def progress(stage: str, current: int = 0, total: int = 0, message: str = "") -> None:
        update_import_job(
            context,
            job_id,
            status="running",
            stage=stage,
            current=int(current or 0),
            total=int(total or 0),
            message=message or stage,
        )

    return progress


def import_job_worker(
    context: WebContext,
    job_id: str,
    filename: str,
    upload_path: Path,
    mime: str,
    role: str = "main",
) -> None:
    update_import_job(context, job_id, status="running", stage="starting", message="Preparando conversión...")
    try:
        imported = import_document_path(filename, upload_path, mime=mime, progress=import_progress_for(context, job_id))
        update_import_job(
            context,
            job_id,
            status="running",
            stage="loading",
            current=0,
            total=0,
            message="Cargando texto convertido en el lector...",
        )
        result = load_imported_document(context, imported, role=role)
        update_import_job(
            context,
            job_id,
            status="done",
            stage="done",
            current=1,
            total=1,
            percent=100,
            message=f"{filename} {'agregado como consulta' if result.get('role') == 'reference' else 'cargado'}. {result.get('total') or 0} bloques listos.",
            result=result,
        )
    except Exception as exc:
        update_import_job(
            context,
            job_id,
            status="error",
            stage="error",
            message="No pude convertir el documento.",
            error=type(exc).__name__,
        )
    finally:
        upload_path.unlink(missing_ok=True)


def get_import_job(context: WebContext, job_id: str) -> dict | None:
    job = context.import_jobs.get(job_id)
    return dict(job) if job else None


def prune_pdf_to_docx(context: WebContext) -> int:
    return context.pdf_downloads.prune()


def register_pdf_to_docx_download(
    context: WebContext,
    saved_path: Path,
    filename: str,
    result: ConversionResult,
) -> dict:
    job_id = uuid.uuid4().hex[:16]
    item = {
        "id": job_id,
        "path": str(saved_path),
        "filename": filename,
        "created_ts": time.time(),
        "pages": result.pages,
        "warnings": list(result.warnings),
    }
    context.pdf_downloads.add(job_id, item)
    return dict(item)


def get_pdf_to_docx_download(context: WebContext, job_id: str) -> dict | None:
    item = context.pdf_downloads.get(job_id)
    return dict(item) if item else None


__all__ = [
    "get_import_job",
    "get_pdf_to_docx_download",
    "import_job_worker",
    "import_progress_for",
    "new_import_job",
    "prune_import_jobs",
    "prune_pdf_to_docx",
    "register_pdf_to_docx_download",
    "update_import_job",
]
