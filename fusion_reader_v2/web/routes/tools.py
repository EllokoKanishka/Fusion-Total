from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2.config import Settings
from fusion_reader_v2.observability import get_logger
from fusion_reader_v2.output_reservation import reserve_output_path
from fusion_reader_v2.pdf_to_docx import JobStatus, convert_pdf_to_docx, safe_output_name
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.downloads import OutputValidationError, stream_file, validate_output_file
from fusion_reader_v2.web.jobs import get_import_job, get_pdf_to_docx_download, register_pdf_to_docx_download


class ToolsResponder(Protocol):
    @property
    def context(self) -> WebContext: ...

    @property
    def settings(self) -> Settings: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _read_multipart_file(self, *, field_name: str) -> tuple[str, str, Path]: ...

    request_id: str


def _run_pdf_job(context: WebContext, request_id: str, job: JobStatus, input_path: Path) -> None:
    temp_docx = context.pdf_root / f"{job.job_id}.docx"
    try:
        result = convert_pdf_to_docx(input_path, temp_docx, job=job)
        if not result.ok:
            job.state = "error"
            job.error = result.error or "Error desconocido en conversión."
            return
        if job.cancelled:
            job.state = "cancelled"
            return
        reservation = reserve_output_path(context.settings.paths.downloads, job.filename, default_suffix=".docx")
        final_target = reservation.publish(temp_docx)
        download_item = register_pdf_to_docx_download(context, final_target, final_target.name, result)
        job.state = "done"
        job.saved_path = str(final_target)
        job.filename = final_target.name
        job.download_url = f"/api/tools/pdf-to-docx/download/{download_item['id']}"
        job.warnings = list(result.warnings)
    except Exception as exc:
        job.state = "error"
        if "reservation" in locals():
            reservation.cleanup()
        job.error = type(exc).__name__
        get_logger().exception("pdf conversion failed", extra={"request_id": request_id, "job_id": job.job_id})
    finally:
        job.updated_ts = time.time()
        input_path.unlink(missing_ok=True)
        if job.state != "done":
            temp_docx.unlink(missing_ok=True)


def handle_tools_get(responder: ToolsResponder, path: str, raw_path: str) -> bool:
    if path == "/api/import-status":
        params = parse_qs(urlparse(raw_path).query)
        job_id = str((params.get("id") or [""])[0])
        import_job = get_import_job(responder.context, job_id)
        if not import_job:
            responder._json(404, {"ok": False, "error": "import_job_not_found"})
        else:
            responder._json(200, import_job)
        return True
    if path.startswith("/api/tools/pdf-to-docx/status/"):
        pdf_job = responder.context.pdf_jobs.get(path.split("/")[-1])
        if not pdf_job:
            responder._json(404, {"ok": False, "error": "Job no encontrado."})
            return True
        responder._json(
            200,
            {
                "ok": True,
                "job_id": pdf_job.job_id,
                "state": pdf_job.state,
                "stage": pdf_job.stage,
                "current_page": pdf_job.current_page,
                "total_pages": pdf_job.total_pages,
                "percent": pdf_job.percent,
                "message": pdf_job.message,
                "filename": pdf_job.filename,
                "saved_path": pdf_job.saved_path,
                "download_url": pdf_job.download_url,
                "warnings": pdf_job.warnings,
                "error": pdf_job.error,
                "noise_lines_removed": pdf_job.result.noise_lines_removed if pdf_job.result else 0,
                "paragraphs_merged": pdf_job.result.paragraphs_merged if pdf_job.result else 0,
                "headings_detected": pdf_job.result.headings_detected if pdf_job.result else 0,
            },
        )
        return True
    if path.startswith("/api/tools/pdf-to-docx/download/"):
        item = get_pdf_to_docx_download(responder.context, Path(path).name)
        if not item:
            responder._json(404, {"ok": False, "error": "pdf_to_docx_download_not_found"})
            return True
        try:
            try:
                docx_path = validate_output_file(
                    str(item.get("path") or ""), responder.settings.paths.downloads, suffix=".docx"
                )
            except OutputValidationError:
                docx_path = validate_output_file(
                    str(item.get("path") or ""), responder.context.pdf_root, suffix=".docx"
                )
        except OutputValidationError:
            responder._json(404, {"ok": False, "error": "pdf_to_docx_file_missing"})
            return True
        stream_file(
            responder,
            docx_path,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=str(item.get("filename") or "documento.docx"),
        )
        return True
    return False


def handle_tools_post(responder: ToolsResponder, path: str) -> bool:
    if path == "/api/tools/pdf-to-docx":
        filename, _mime, input_path = responder._read_multipart_file(field_name="file")
        clean_name = Path(filename).name
        if Path(clean_name).suffix.lower() != ".pdf":
            input_path.unlink(missing_ok=True)
            responder._json(400, {"ok": False, "error": "Solo se aceptan archivos PDF."})
            return True
        responder.context.pdf_root.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex[:16]
        owned_input = responder.context.pdf_root / f"{job_id}.pdf"
        os.replace(input_path, owned_input)
        pdf_job = JobStatus(job_id=job_id, filename=safe_output_name(clean_name))
        responder.context.pdf_jobs.add(job_id, pdf_job)
        responder.context.start_thread(
            target=_run_pdf_job,
            args=(responder.context, responder.request_id, pdf_job, owned_input),
            name=f"fusion-pdf-to-docx-{job_id}",
        )
        responder._json(200, {"ok": True, "job_id": job_id})
        return True
    if path.startswith("/api/tools/pdf-to-docx/cancel/"):
        job_id = path.split("/")[-1]
        cancelled_job = responder.context.pdf_jobs.get(job_id)
        if not cancelled_job:
            responder._json(404, {"ok": False, "error": "Job no encontrado."})
            return True
        cancelled_job.cancelled = True
        cancelled_job.state = "cancelled"
        responder._json(200, {"ok": True, "job_id": job_id})
        return True
    return False


__all__ = ["handle_tools_get", "handle_tools_post"]
