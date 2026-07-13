from __future__ import annotations

import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from fusion_reader_v2 import FusionReaderV2, import_document_path
from fusion_reader_v2.config import Settings, create_settings, environment_value
from fusion_reader_v2.documents import safe_filename
from fusion_reader_v2.observability import configure_logging, get_logger
from fusion_reader_v2.output_reservation import reserve_output_path
from fusion_reader_v2.owned_subprocess import run_owned
from fusion_reader_v2.web.errors import error_response
from fusion_reader_v2.web.context import WebContext
from fusion_reader_v2.web.routes.documents import (
    library_items,
    load_imported_document,
    resolve_library_path as resolve_library_path,
)
from fusion_reader_v2.web.routes.health import handle_health_get
from fusion_reader_v2.web.routes.audio import handle_audio_get, handle_audio_post
from fusion_reader_v2.web.routes.notes import handle_notes_get, handle_notes_post
from fusion_reader_v2.web.routes.tools import handle_tools_get
from fusion_reader_v2.web.routes.dialogue import handle_dialogue_get, handle_dialogue_post
from fusion_reader_v2.web.routes.reading import handle_reading_post
from fusion_reader_v2.web.jobs import import_job_worker, new_import_job, register_pdf_to_docx_download
from fusion_reader_v2.web.downloads import (
    audio_url_for,
    cached_audio_path,
    unique_download_target as unique_download_target,
)
from fusion_reader_v2.web.routing import create_router
from fusion_reader_v2.pdf_to_docx import (
    JobStatus,
    convert_pdf_to_docx,
    safe_output_name,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
PORT = 8010
UPLOAD_TEMP_SUFFIXES = {
    ".bin": ".bin",
    ".doc": ".doc",
    ".docx": ".docx",
    ".html": ".html",
    ".md": ".md",
    ".odt": ".odt",
    ".pdf": ".pdf",
    ".rtf": ".rtf",
    ".txt": ".txt",
    ".wav": ".wav",
    ".webm": ".webm",
}
# Compatibility sentinel: application state is owned by each WebContext.
APP: FusionReaderV2 | None = None
ROUTER = create_router()


def _load_static_text(filename: str, fallback: str = "") -> str:
    try:
        return (STATIC_ROOT / filename).read_text(encoding="utf-8")
    except OSError:
        return fallback


INDEX_HTML = _load_static_text(
    "index.html",
    "<!doctype html><html><body><h1>Fusion Reader v2</h1><p>Static assets unavailable.</p></body></html>",
)
RUNTIME_INFO = {
    "app": "fusion_reader_v2",
    "commit": environment_value("FUSION_READER_COMMIT", "unknown") or "unknown",
    "pid": os.getpid(),
    "port": PORT,
    "cwd": "",
    "started_at": "",
    "server_file": __file__,
    "python": "",
    "log_file": "",
}


def _get_git_commit(repository: Path) -> str:
    try:
        result = run_owned(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


class FusionHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    context: WebContext

    def server_close(self) -> None:
        super().server_close()
        if hasattr(self, "context"):
            outcome = self.context.shutdown_jobs(timeout=10.0)
            if not outcome["ok"]:
                raise RuntimeError(f"web_shutdown_timeout:{','.join(outcome['alive_threads'])}")
            app_outcome = self.context.app.shutdown_background_work(timeout=10.0)
            if isinstance(app_outcome, dict) and not app_outcome.get("ok", True):
                raise RuntimeError("app_shutdown_timeout")


class Handler(BaseHTTPRequestHandler):
    server_version = "FusionReaderV2/0.1"

    @property
    def context(self) -> WebContext:
        return self.server.context  # type: ignore[attr-defined]

    @property
    def app(self) -> FusionReaderV2:
        return self.context.app

    @property
    def settings(self) -> Settings:
        return self.context.settings

    def log_message(self, message_format: str, *args: object) -> None:
        get_logger().info(
            "http client=%s message=%s",
            self.address_string(),
            message_format % args,
            extra={"request_id": getattr(self, "request_id", "-")},
        )

    def setup(self) -> None:
        super().setup()
        self.request_id = uuid.uuid4().hex
        self.connection.settimeout(30.0)

    def _send(self, status: int, content_type: str, raw: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Request-ID", self.request_id)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self'; media-src 'self' blob:; "
            "connect-src 'self'; img-src 'self' data:",
        )
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _json(self, status: int, payload: dict) -> None:
        out = dict(payload)
        if status >= 400 or out.get("ok") is False:
            out["ok"] = False
            out.setdefault("error", "request_failed")
            out.setdefault("detail", str(out.get("error") or "request_failed"))
            out.setdefault("request_id", self.request_id)
        raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", raw)

    def _result(self, status: int, payload: dict) -> None:
        out = dict(payload)
        if out.get("audio"):
            out["audio_url"] = audio_url_for(self.context, str(out.get("audio") or ""))
        self._json(status, out)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        limit = self.settings.limits.upload_max_bytes
        if length > limit:
            raise ValueError("request_body_too_large")
        content_type = (self.headers.get("Content-Type", "") or "").lower()
        if "application/json" not in content_type:
            raise ValueError("application_json_required")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_json") from exc
        if not isinstance(payload, dict):
            raise ValueError("json_object_required")
        return payload

    def _read_body_to_temp(self, filename: str) -> Path:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("missing_file_data")
        limit = self.settings.limits.upload_max_bytes
        if Path(safe_filename(filename)).suffix.lower() == ".pdf":
            limit = self.settings.limits.pdf_max_bytes
        if length > limit:
            raise ValueError("upload_too_large")
        self.context.upload_root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="fusion_reader_upload_", suffix=".upload", dir=self.context.upload_root)
        path = Path(name)
        remaining = length
        try:
            with os.fdopen(fd, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if remaining:
            path.unlink(missing_ok=True)
            raise ValueError("incomplete_upload")
        return path

    def _read_multipart_file(
        self,
        field_name: str = "file",
        max_bytes: int | None = None,
    ) -> tuple[str, str, Path]:
        content_type = self.headers.get("Content-Type", "") or ""
        match = re.search(r'boundary="?([^";]+)"?', content_type)
        if "multipart/form-data" not in content_type or not match:
            raise ValueError("multipart_required")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("missing_file_data")
        limit = self.settings.limits.pdf_max_bytes if max_bytes is None else int(max_bytes)
        if length > limit:
            raise ValueError("pdf_too_large")
        boundary = match.group(1).encode("utf-8")
        opening = b"--" + boundary
        remaining = length

        def read_line() -> bytes:
            nonlocal remaining
            if remaining <= 0:
                return b""
            line = self.rfile.readline(min(remaining, 64 * 1024 + 1))
            remaining -= len(line)
            if len(line) > 64 * 1024:
                raise ValueError("multipart_header_too_large")
            return line

        if read_line().rstrip(b"\r\n") != opening:
            raise ValueError("invalid_multipart_boundary")
        headers: list[bytes] = []
        while True:
            line = read_line()
            if line in {b"\r\n", b"\n"}:
                break
            if not line:
                raise ValueError("incomplete_multipart_headers")
            headers.append(line)
        headers_text = b"".join(headers).decode("utf-8", errors="replace")
        disposition = next(
            (line for line in headers_text.splitlines() if line.lower().startswith("content-disposition:")),
            "",
        )
        if f'name="{field_name}"' not in disposition:
            raise ValueError("missing_file_field")
        filename_match = re.search(r'filename="([^"]+)"', disposition)
        filename = Path(filename_match.group(1)).name if filename_match else "documento.pdf"
        mime_match = re.search(r"^Content-Type:\s*([^\r\n]+)", headers_text, flags=re.IGNORECASE | re.MULTILINE)
        mime = str(mime_match.group(1)).strip() if mime_match else "application/pdf"
        suffix = UPLOAD_TEMP_SUFFIXES.get(Path(safe_filename(filename)).suffix.lower(), ".bin")
        self.context.upload_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="fusion_reader_multipart_",
            suffix=suffix,
            dir=self.context.upload_root,
        )
        temporary = Path(temporary_name)
        marker = b"\r\n--" + boundary
        buffer = b""
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("incomplete_upload")
                    remaining -= len(chunk)
                    buffer += chunk
                    marker_index = buffer.find(marker)
                    if marker_index >= 0:
                        handle.write(buffer[:marker_index])
                        handle.flush()
                        os.fsync(handle.fileno())
                        return filename, mime, temporary
                    safe_length = len(buffer) - len(marker) - 4
                    if safe_length > 0:
                        handle.write(buffer[:safe_length])
                        buffer = buffer[safe_length:]
            raise ValueError("incomplete_multipart_boundary")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if ROUTER.resolve("GET", path) is None:
            self._json(404, {"ok": False, "error": "not_found", "detail": "La ruta no existe."})
            return
        if path == "/":
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            return
        if path.startswith("/static/"):
            filename = unquote(path.removeprefix("/static/"))
            allowed = {
                "styles.css": "text/css; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "busy_controls.js": "text/javascript; charset=utf-8",
                "js/bootstrap.mjs": "text/javascript; charset=utf-8",
                "js/api.mjs": "text/javascript; charset=utf-8",
                "js/audio.mjs": "text/javascript; charset=utf-8",
                "js/busy.mjs": "text/javascript; charset=utf-8",
                "js/dialogue.mjs": "text/javascript; charset=utf-8",
            }
            content_type = allowed.get(filename)
            asset = STATIC_ROOT / filename
            if not content_type or not asset.is_file():
                self._json(404, {"ok": False, "error": "static_asset_not_found"})
                return
            self._send(200, content_type, asset.read_bytes())
            return
        if handle_health_get(self, path):
            return
        if path == "/api/library":
            self._json(200, {"ok": True, "items": library_items(self.context)})
            return
        if handle_audio_get(self, path, self.path):
            return
        if path == "/api/prepare/status":
            self._json(200, self.app.prepare_status())
            return
        if path == "/api/references":
            self._json(200, self.app.list_reference_documents())
            return
        if handle_notes_get(self, path, self.path):
            return
        if handle_dialogue_get(self, path):
            return
        if handle_tools_get(self, path, self.path):
            return
        self._json(404, {"ok": False, "error": "not_found"})

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if ROUTER.resolve("HEAD", path) is None:
            self.send_response(404)
            self.end_headers()
            return
        if path == "/":
            raw = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return
        if path.startswith("/static/"):
            filename = unquote(path.removeprefix("/static/"))
            asset = STATIC_ROOT / filename
            if (
                filename
                not in {
                    "styles.css",
                    "app.js",
                    "busy_controls.js",
                    "js/bootstrap.mjs",
                    "js/api.mjs",
                    "js/audio.mjs",
                    "js/busy.mjs",
                    "js/dialogue.mjs",
                }
                or not asset.is_file()
            ):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(asset.stat().st_size))
            self.end_headers()
            return
        if path in ("/health", "/api/status"):
            st = self.context.status()
            raw = json.dumps(st).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return
        if path.startswith("/audio/"):
            audio_path = cached_audio_path(self.context, path)
            if not audio_path:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(audio_path.stat().st_size))
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if ROUTER.resolve("POST", path) is None:
            self._json(404, {"ok": False, "error": "not_found", "detail": "La ruta no existe."})
            return
        try:
            if path == "/api/tools/pdf-to-docx":
                filename, mime, input_path = self._read_multipart_file(field_name="file")
                clean_name = Path(filename).name
                if Path(clean_name).suffix.lower() != ".pdf":
                    input_path.unlink(missing_ok=True)
                    self._json(400, {"ok": False, "error": "Solo se aceptan archivos PDF."})
                    return
                self.context.pdf_root.mkdir(parents=True, exist_ok=True)
                job_id = uuid.uuid4().hex[:16]
                owned_input = self.context.pdf_root / f"{job_id}.pdf"
                os.replace(input_path, owned_input)
                input_path = owned_input

                pdf_job = JobStatus(job_id=job_id, filename=safe_output_name(clean_name))
                self.context.pdf_jobs.add(job_id, pdf_job)

                def _run_job(j: JobStatus, in_p: Path, original_name: str):
                    temp_docx = self.context.pdf_root / f"{j.job_id}.docx"
                    try:
                        result = convert_pdf_to_docx(in_p, temp_docx, job=j)
                        if not result.ok:
                            j.state = "error"
                            j.error = result.error or "Error desconocido en conversión."
                            return

                        if j.cancelled:
                            j.state = "cancelled"
                            return

                        reservation = reserve_output_path(
                            self.context.settings.paths.downloads, j.filename, default_suffix=".docx"
                        )
                        final_target = reservation.publish(temp_docx)
                        download_item = register_pdf_to_docx_download(
                            self.context,
                            final_target,
                            final_target.name,
                            result,
                        )

                        j.state = "done"
                        j.saved_path = str(final_target)
                        j.filename = final_target.name
                        j.download_url = f"/api/tools/pdf-to-docx/download/{download_item['id']}"
                        j.warnings = list(result.warnings)
                    except Exception as exc:
                        j.state = "error"
                        if "reservation" in locals():
                            reservation.cleanup()
                        j.error = type(exc).__name__
                        get_logger().exception(
                            "pdf conversion failed",
                            extra={"request_id": self.request_id, "job_id": j.job_id},
                        )
                    finally:
                        j.updated_ts = time.time()
                        in_p.unlink(missing_ok=True)
                        if j.state != "done":
                            temp_docx.unlink(missing_ok=True)

                self.context.start_thread(
                    target=_run_job,
                    args=(pdf_job, input_path, clean_name),
                    name=f"fusion-pdf-to-docx-{job_id}",
                )
                self._json(200, {"ok": True, "job_id": job_id})
                return

            if path.startswith("/api/tools/pdf-to-docx/cancel/"):
                job_id = path.split("/")[-1]
                cancelled_job = self.context.pdf_jobs.get(job_id)
                if not cancelled_job:
                    self._json(404, {"ok": False, "error": "Job no encontrado."})
                    return
                cancelled_job.cancelled = True
                cancelled_job.state = "cancelled"
                self._json(200, {"ok": True, "job_id": job_id})
                return
            if path == "/api/import-file/start":
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["documento"])[0])
                mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
                role = str((params.get("role") or ["main"])[0])
                tmp_path = self._read_body_to_temp(filename)
                import_job = new_import_job(
                    self.context,
                    filename,
                    mime,
                    tmp_path,
                    tmp_path.stat().st_size,
                    role=role,
                )
                self.context.start_thread(
                    target=import_job_worker,
                    args=(self.context, str(import_job["job_id"]), filename, tmp_path, mime, role),
                    name=f"fusion-import-{import_job['job_id']}",
                )
                self._json(202, import_job)
                return
            if path == "/api/import-file":
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["documento"])[0])
                mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
                role = str((params.get("role") or ["main"])[0])
                tmp_path = self._read_body_to_temp(filename)
                try:
                    imported = import_document_path(filename, tmp_path, mime=mime)
                finally:
                    tmp_path.unlink(missing_ok=True)
                self._json(200, load_imported_document(self.context, imported, role=role))
                return
            if path == "/api/dialogue/turn" and "application/json" not in (self.headers.get("Content-Type", "") or ""):
                content_type = self.headers.get("Content-Type", "") or ""
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["dialogue.webm"])[0])
                raw_chunk_index = (params.get("chunk_index") or [""])[0]
                chunk_index = int(raw_chunk_index) if raw_chunk_index else None
                audio_meta = {
                    "audio_size_bytes": str((params.get("audio_size_bytes") or [""])[0]),
                    "capture_ms": str((params.get("capture_ms") or [""])[0]),
                    "mic_rms": str((params.get("mic_rms") or [""])[0]),
                    "mic_peak": str((params.get("mic_peak") or [""])[0]),
                    "voice_detected": str((params.get("voice_detected") or [""])[0]),
                    "cut_reason": str((params.get("cut_reason") or [""])[0]),
                }
                tmp_path = self._read_body_to_temp(filename)
                try:
                    self._result(
                        200,
                        self.app.dialogue_turn_audio(
                            tmp_path,
                            mime=content_type,
                            model=str((params.get("model") or [""])[0]),
                            chunk_index=chunk_index,
                            audio_meta=audio_meta,
                        ),
                    )
                finally:
                    tmp_path.unlink(missing_ok=True)
                return
            payload = self._payload()
            if handle_audio_post(self, path, payload):
                return
            if handle_dialogue_post(self, path, payload):
                return
            if handle_reading_post(self, path, payload):
                return
            if handle_notes_post(self, path, payload):
                return
        except Exception as exc:
            get_logger().warning(
                "request failed error=%s",
                type(exc).__name__,
                extra={"request_id": self.request_id},
            )
            status, payload = error_response(exc, self.request_id)
            self._json(status, payload)
            return
        self._json(404, {"ok": False, "error": "not_found"})


def create_http_server(app: FusionReaderV2, settings: Settings) -> ThreadingHTTPServer:
    settings.validate()
    configure_logging(settings.paths.logs / "fusion_reader_v2_server.log")
    runtime_info = {
        "app": "fusion_reader_v2",
        "commit": _get_git_commit(settings.paths.repository),
        "pid": os.getpid(),
        "port": settings.ports.api,
        "cwd": str(settings.paths.repository),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "server_file": __file__,
        "python": sys.executable,
        "log_file": str(settings.paths.logs / "fusion_reader_v2_server.log"),
    }
    context = WebContext(app=app, settings=settings, runtime_info=runtime_info)
    server = FusionHTTPServer((settings.security.bind_host, settings.ports.api), Handler)
    server.context = context
    return server


def main() -> None:
    from fusion_reader_v2.composition import create_fusion_reader

    settings = create_settings()
    app = create_fusion_reader(settings)
    server = create_http_server(app, settings)

    def request_shutdown(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, name="fusion-http-shutdown").start()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    print(f"Fusion Reader v2 API listening on http://{settings.security.bind_host}:{settings.ports.api}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        app.shutdown_background_work(timeout=10.0)


__all__ = [
    "Handler",
    "INDEX_HTML",
    "PORT",
    "ROOT",
    "RUNTIME_INFO",
    "create_http_server",
    "main",
]
