from __future__ import annotations

import time
import uuid
from pathlib import Path

from fusion_reader_v2 import import_document_path
from fusion_reader_v2.documents import document_conversion
from fusion_reader_v2.pdf_to_docx import ConversionResult
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.errors import APIError
from fusion_reader_v2.web.routes.documents import _load_imported_document


def new_import_job(
    context: WebContext,
    filename: str,
    mime: str,
    upload_path: Path,
    size_bytes: int,
    role: str = "main",
    *,
    import_token: dict | None = None,
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
    with context.app._document_lock:
        context.import_jobs.add(job_id, job)
        try:
            job.update(context.reserve_import(role, import_token))
        except Exception:
            context.import_jobs.remove(job_id)
            raise
    return dict(job)


def start_import_job(
    context: WebContext,
    filename: str,
    mime: str,
    upload_path: Path,
    role: str = "main",
    *,
    import_token: dict | None = None,
) -> dict:
    # A failed registration/start must not supersede an already-running job.
    with context.app._document_lock:
        previous_generation = context._main_import_generation
        job = None
        try:
            job = new_import_job(
                context, filename, mime, upload_path, upload_path.stat().st_size, role, import_token=import_token
            )
            context.start_thread(
                target=import_job_worker,
                args=(context, job["job_id"], filename, upload_path, mime, role),
                name=f"fusion-import-{job['job_id']}",
            )
        except Exception:
            upload_path.unlink(missing_ok=True)
            if job is not None:
                context._main_import_generation = previous_generation
                update_import_job(
                    context,
                    job["job_id"],
                    status="error",
                    stage="error",
                    error="worker_start_failed",
                    message="No pude iniciar la conversión.",
                )
            raise
        return job


def cancel_import_job(context: WebContext, job_id: str) -> dict | None:
    with context.app._document_lock:

        def cancel(job: dict) -> None:
            if job.get("status") not in {"done", "cancelled", "error"}:
                job.update(
                    cancelled=True,
                    status="canceling",
                    stage="canceling",
                    message="Cancelando importación...",
                    updated_ts=time.time(),
                )

        item = context.import_jobs.update(job_id, cancel)
        return dict(item) if item is not None else None


def prune_import_jobs(context: WebContext) -> int:
    return context.import_jobs.prune()


def update_import_job(context: WebContext, job_id: str, **changes) -> None:
    def update(job: dict) -> None:
        if job.get("status") in {"done", "cancelled", "error"}:
            return
        if job.get("cancelled") and changes.get("status") == "running":
            return
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
    class Signal:
        def is_set(self) -> bool:
            job = get_import_job(context, job_id)
            if context._closed or not job or job.get("cancelled"):
                return True
            serial = job.get("request_generation")
            return job.get("document_generation") != context.app._document_generation or (
                serial is not None and serial != context._main_import_generation
            )

    signal = Signal()
    try:
        token = get_import_job(context, job_id)
        with context.import_publication(token, job_id):
            update_import_job(context, job_id, status="running", stage="starting", message="Preparando conversión...")
        with document_conversion(signal):
            imported = import_document_path(
                filename, upload_path, mime=mime, progress=import_progress_for(context, job_id)
            )
        with context.import_publication(token, job_id):
            # The nested load uses reentrant app locking; the web ownership
            # lock is already held, so call its implementation in this guard.
            result = _load_imported_document(context, imported, role)
            if result.get("ok") is False:
                raise APIError(str(result.get("error") or "import_rejected"), "No pude guardar el documento.", 409)
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
        cancelled = isinstance(exc, APIError) and exc.code in {"import_cancelled", "import_superseded"}
        cancelled = cancelled or signal.is_set()
        update_import_job(
            context,
            job_id,
            status="cancelled" if cancelled else "error",
            stage="cancelled" if cancelled else "error",
            message="Importación descartada por cancelación o cambio de documento."
            if cancelled
            else "No pude convertir el documento.",
            error=exc.code if isinstance(exc, APIError) else type(exc).__name__,
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
    "cancel_import_job",
    "start_import_job",
    "get_pdf_to_docx_download",
    "import_job_worker",
    "import_progress_for",
    "new_import_job",
    "prune_import_jobs",
    "prune_pdf_to_docx",
    "register_pdf_to_docx_download",
    "update_import_job",
]
