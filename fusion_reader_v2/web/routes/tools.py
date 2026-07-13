from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2.config import Settings
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.downloads import OutputValidationError, stream_file, validate_output_file
from fusion_reader_v2.web.jobs import get_import_job, get_pdf_to_docx_download


class ToolsResponder(Protocol):
    @property
    def context(self) -> WebContext: ...

    @property
    def settings(self) -> Settings: ...

    def _json(self, status: int, payload: dict) -> None: ...


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


__all__ = ["handle_tools_get"]
