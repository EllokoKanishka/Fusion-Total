from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from fusion_reader_v2 import FusionReaderV2, import_document_bytes, import_document_path
from fusion_reader_v2.config import Settings, create_settings, environment_value
from fusion_reader_v2.documents import safe_filename
from fusion_reader_v2.domain.jobs import JobRegistry
from fusion_reader_v2.observability import configure_logging, get_logger
from fusion_reader_v2.output_validation import OutputValidationError, stream_file, validate_output_file
from fusion_reader_v2.version import __version__
from fusion_reader_v2.web.errors import error_response
from fusion_reader_v2.web.routing import create_router
from fusion_reader_v2.pdf_to_docx import (
    ConversionResult,
    JobStatus,
    convert_pdf_to_docx,
    safe_output_name,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
PORT = 8010
ALLOWED_LIBRARY_SUFFIXES = {".txt", ".md"}
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


@dataclass
class WebContext:
    app: FusionReaderV2
    settings: Settings
    runtime_info: dict
    import_jobs: JobRegistry[dict] = field(init=False)
    pdf_jobs: JobRegistry[JobStatus] = field(init=False)
    pdf_downloads: JobRegistry[dict] = field(init=False)
    _threads: set[threading.Thread] = field(default_factory=set, init=False)
    _threads_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _closed: bool = field(default=False, init=False)
    _started_monotonic: float = field(default_factory=time.monotonic, init=False)

    def __post_init__(self) -> None:
        limits = self.settings.limits
        self.import_jobs = JobRegistry(
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            is_terminal=lambda item: str(item.get("status") or "") in {"done", "cancelled", "error"},
            updated_at=lambda item: float(item.get("updated_ts") or item.get("created_ts") or 0),
        )
        self.pdf_jobs = JobRegistry(
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            is_terminal=lambda item: str(item.state or "") in {"done", "cancelled", "error"},
            updated_at=lambda item: float(getattr(item, "updated_ts", getattr(item, "created_ts", 0)) or 0),
        )
        self.pdf_downloads = JobRegistry(
            max_items=limits.job_max_items,
            ttl_seconds=limits.job_ttl_seconds,
            is_terminal=lambda _item: True,
            updated_at=lambda item: float(item.get("created_ts") or 0),
        )

    @property
    def library_root(self) -> Path:
        return self.settings.paths.library

    @property
    def converted_root(self) -> Path:
        return self.settings.paths.runtime / "imported_texts"

    @property
    def upload_root(self) -> Path:
        return self.settings.paths.runtime / "upload_jobs"

    @property
    def pdf_root(self) -> Path:
        return self.settings.paths.runtime / "pdf_to_word"

    def start_thread(self, *, target, args: tuple, name: str) -> threading.Thread:
        def run_owned() -> None:
            try:
                target(*args)
            finally:
                with self._threads_lock:
                    self._threads.discard(threading.current_thread())

        with self._threads_lock:
            if self._closed:
                raise RuntimeError("web_context_closed")
            thread = threading.Thread(target=run_owned, name=name, daemon=False)
            self._threads.add(thread)
            thread.start()
            return thread

    def shutdown_jobs(self, timeout: float = 10.0) -> dict:
        with self._threads_lock:
            if self._closed and not self._threads:
                return {"ok": True, "state": "closed", "alive_threads": []}
            self._closed = True
            threads = list(self._threads)
        for job in self.pdf_jobs.snapshot().values():
            if job.state not in {"done", "cancelled", "error"}:
                job.cancelled = True
        for job in self.import_jobs.snapshot().values():
            if str(job.get("status") or "") not in {"done", "cancelled", "error"}:
                job["cancelled"] = True
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        with self._threads_lock:
            alive = sorted(thread.name for thread in self._threads if thread.is_alive())
            if not alive:
                self._threads.clear()
        return {
            "ok": not alive,
            "state": "closed" if not alive else "timeout",
            "alive_threads": alive,
        }

    def status(self) -> dict:
        status = self.app.status()
        services = dict(status.get("services") or {})
        degradations = [
            name
            for name in ("tts", "stt", "chat", "external_research")
            if not bool((services.get(name) or {}).get("ready", (services.get(name) or {}).get("ok")))
        ]
        persistence_warnings = [
            warning.code for warning in getattr(getattr(self.app, "_session_store", None), "warnings", ())
        ]
        status.update(
            {
                "version": __version__,
                "commit": self.runtime_info.get("commit", "unknown"),
                "pid": self.runtime_info.get("pid", os.getpid()),
                "uptime_seconds": round(time.monotonic() - self._started_monotonic, 3),
                "state_schema": 1,
                "cache": self.app.cache.inspect(),
                "jobs": {
                    "imports": len(self.import_jobs),
                    "pdf_to_docx": len(self.pdf_jobs),
                    "pdf_downloads": len(self.pdf_downloads),
                    "audio_export": status.get("audio_export", {}),
                    "prepare": status.get("prepare", {}),
                },
                "providers": services,
                "ports": {
                    "api": self.settings.ports.api,
                    "tts_gpu": self.settings.ports.tts_gpu,
                    "tts_cpu": self.settings.ports.tts_cpu,
                    "stt": self.settings.ports.stt,
                    "ollama": self.settings.ports.ollama,
                    "searxng": self.settings.ports.searxng,
                },
                "warnings": persistence_warnings,
                "degradations": degradations,
                "runtime": self.runtime_info,
            }
        )
        return status


def _get_git_commit(repository: Path) -> str:
    import subprocess

    try:
        result = subprocess.run(
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


def audio_url_for(context: WebContext, path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve()
    cache_root = context.app.cache.root.resolve()
    if path.parent != cache_root or not path.exists():
        return ""
    return f"/audio/{path.name}"


def cached_audio_path(context: WebContext, url_path: str) -> Path | None:
    filename = Path(unquote(url_path.removeprefix("/audio/"))).name
    audio_path = (context.app.cache.root / filename).resolve()
    cache_root = context.app.cache.root.resolve()
    if audio_path.parent != cache_root or not audio_path.exists():
        return None
    return audio_path


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


def unique_download_target(context: WebContext, filename: str) -> Path:
    downloads_dir = context.settings.paths.downloads
    downloads_dir.mkdir(parents=True, exist_ok=True)
    candidate = downloads_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix or ".docx"
    for index in range(2, 1000):
        alt = downloads_dir / f"{stem}_{index}{suffix}"
        if not alt.exists():
            return alt
    raise RuntimeError("no_safe_output_slot")


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
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; media-src 'self' blob:; "
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

    def _remote_mutation_authorized(self) -> bool:
        if not self.settings.security.allow_remote:
            return True
        expected = self.settings.security.api_token.encode("utf-8")
        authorization = self.headers.get("Authorization", "")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not supplied:
            supplied = self.headers.get("X-Fusion-Token", "").strip()
        return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if ROUTER.resolve("GET", path) is None:
            self._json(404, {"ok": False, "error": "not_found", "detail": "La ruta no existe."})
            return
        if path == "/":
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            return
        if path.startswith("/static/"):
            filename = Path(unquote(path.removeprefix("/static/"))).name
            allowed = {
                "styles.css": "text/css; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "busy_controls.js": "text/javascript; charset=utf-8",
            }
            content_type = allowed.get(filename)
            asset = STATIC_ROOT / filename
            if not content_type or not asset.is_file():
                self._json(404, {"ok": False, "error": "static_asset_not_found"})
                return
            self._send(200, content_type, asset.read_bytes())
            return
        if path == "/health/live":
            self._json(200, {"ok": True, "status": "live", "pid": os.getpid()})
            return
        if path == "/health/ready":
            status = self.context.status()
            services = status.get("services", {})
            degradations = [
                name
                for name in ("tts", "stt", "chat", "external_research")
                if not bool((services.get(name) or {}).get("ready", (services.get(name) or {}).get("ok")))
            ]
            self._json(
                200,
                {
                    "ok": True,
                    "status": "ready",
                    "reader_ready": True,
                    "services": services,
                    "degradations": degradations,
                },
            )
            return
        if path in ("/health", "/api/status"):
            self._json(200, self.context.status())
            return
        if path == "/api/build":
            self._json(200, {"ok": True, **self.context.runtime_info})
            return
        if path == "/api/library":
            self._json(200, {"ok": True, "items": library_items(self.context)})
            return
        if path == "/api/voice/voices" or path == "/api/voices":
            self._json(200, self.app.get_voice_catalog())
            return
        if path == "/api/voice/metrics":
            self._json(200, self.app.recent_voice_metrics())
            return
        if path == "/api/voice/metrics/summary":
            self._json(200, self.app.voice_metrics_summary())
            return
        if path == "/api/voice/metrics/documents":
            self._json(200, self.app.voice_metrics_by_document())
            return
        if path == "/api/voice/metrics/chunks":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            doc_id = str((params.get("doc_id") or [""])[0])
            limit = int((params.get("limit") or ["20"])[0])
            self._json(200, self.app.voice_metrics_by_chunk(doc_id=doc_id, limit=limit))
            return
        if path == "/api/prepare/status":
            self._json(200, self.app.prepare_status())
            return
        if path == "/api/audio-export/status":
            self._json(200, self.app.audio_export_overview())
            return
        if path == "/api/references":
            self._json(200, self.app.list_reference_documents())
            return
        if path == "/api/notes":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            doc_id = str((params.get("doc_id") or [""])[0])
            current_only = str((params.get("current_only") or ["0"])[0]).lower() in {"1", "true", "yes"}
            chunk_index_raw = str((params.get("chunk_index") or [""])[0])
            chunk_index = int(chunk_index_raw) if chunk_index_raw else None
            self._json(200, self.app.list_notes(doc_id=doc_id, chunk_index=chunk_index, current_only=current_only))
            return
        if path == "/api/dialogue/status":
            self._json(200, self.app.dialogue_status())
            return
        if path == "/api/import-status":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            job_id = str((params.get("id") or [""])[0])
            import_job = get_import_job(self.context, job_id)
            if not import_job:
                self._json(404, {"ok": False, "error": "import_job_not_found"})
                return
            self._json(200, import_job)
            return
        if path.startswith("/api/tools/pdf-to-docx/status/"):
            job_id = path.split("/")[-1]
            pdf_job = self.context.pdf_jobs.get(job_id)
            if not pdf_job:
                self._json(404, {"ok": False, "error": "Job no encontrado."})
                return
            self._json(
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
            return

        if path.startswith("/api/tools/pdf-to-docx/download/"):
            job_id = Path(path).name
            item = get_pdf_to_docx_download(self.context, job_id)
            if not item:
                self._json(404, {"ok": False, "error": "pdf_to_docx_download_not_found"})
                return
            try:
                try:
                    docx_path = validate_output_file(
                        str(item.get("path") or ""), self.settings.paths.downloads, suffix=".docx"
                    )
                except OutputValidationError:
                    docx_path = validate_output_file(str(item.get("path") or ""), self.context.pdf_root, suffix=".docx")
            except OutputValidationError:
                self._json(404, {"ok": False, "error": "pdf_to_docx_file_missing"})
                return
            stream_file(
                self,
                docx_path,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=str(item.get("filename") or "documento.docx"),
            )
            return
        if path.startswith("/api/audio-export/status/"):
            job_id = Path(path).name
            status = self.app.audio_export_status(job_id)
            self._json(200 if status.get("ok") else 404, status)
            return
        if path.startswith("/api/audio-export/download/"):
            job_id = Path(path).name
            item = self.app.get_audio_export_download(job_id)
            if not item.get("ok"):
                self._json(404, item)
                return
            try:
                audio_root = Path(getattr(self.app, "audio_export_root", self.settings.paths.downloads))
                wav_path = validate_output_file(
                    str(item.get("path") or ""), audio_root, suffix=".wav"
                )
            except OutputValidationError:
                self._json(404, {"ok": False, "error": "audio_export_file_missing"})
                return
            stream_file(self, wav_path, content_type="audio/wav", filename=str(item.get("filename") or "audio.wav"))
            return
        if path.startswith("/audio/"):
            audio_path = cached_audio_path(self.context, path)
            if not audio_path:
                self._json(404, {"ok": False, "error": "audio_not_found"})
                return
            self._send(200, "audio/wav", audio_path.read_bytes())
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
            filename = Path(unquote(path.removeprefix("/static/"))).name
            asset = STATIC_ROOT / filename
            if filename not in {"styles.css", "app.js", "busy_controls.js"} or not asset.is_file():
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
        if not self._remote_mutation_authorized():
            self._json(401, {"ok": False, "error": "api_token_required"})
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

                        final_target = unique_download_target(self.context, j.filename)
                        os.replace(temp_docx, final_target)
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
            if path == "/api/load":
                role = str(payload.get("role") or "main")
                if payload.get("book_id"):
                    if role == "reference":
                        self._json(
                            200,
                            self.app.add_reference_file(
                                resolve_library_path(self.context, str(payload.get("book_id")))
                            ),
                        )
                    else:
                        self._json(
                            200,
                            self.app.load_file(
                                resolve_library_path(self.context, str(payload.get("book_id"))), prefetch=False
                            ),
                        )
                    return
                if payload.get("text"):
                    if role == "reference":
                        self._json(
                            200,
                            self.app.add_reference_text(
                                str(payload.get("doc_id") or "manual"),
                                str(payload.get("title") or "Manual"),
                                str(payload.get("text")),
                                source_type="manual",
                            ),
                        )
                    else:
                        self._json(
                            200,
                            self.app.load_text(
                                str(payload.get("doc_id") or "manual"),
                                str(payload.get("title") or "Manual"),
                                str(payload.get("text")),
                                prefetch=False,
                                source_type="manual",
                            ),
                        )
                    return
                if payload.get("path"):
                    if role == "reference":
                        self._json(
                            200,
                            self.app.add_reference_file(resolve_library_path(self.context, str(payload.get("path")))),
                        )
                    else:
                        self._json(
                            200,
                            self.app.load_file(
                                resolve_library_path(self.context, str(payload.get("path"))), prefetch=False
                            ),
                        )
                    return
                self._json(400, {"ok": False, "error": "missing_text_or_book_id"})
                return
            if path == "/api/import":
                filename = str(payload.get("filename") or "documento")
                mime = str(payload.get("mime") or "")
                role = str(payload.get("role") or "main")
                raw_b64 = str(payload.get("data_b64") or "")
                if not raw_b64:
                    self._json(400, {"ok": False, "error": "missing_file_data"})
                    return
                if len(raw_b64) > (self.settings.limits.upload_max_bytes * 4 // 3) + 8:
                    raise ValueError("base64_upload_too_large")
                try:
                    decoded = base64.b64decode(raw_b64, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("invalid_base64") from exc
                if len(decoded) > self.settings.limits.upload_max_bytes:
                    raise ValueError("upload_too_large")
                imported = import_document_bytes(filename, decoded, mime=mime)
                self._json(200, load_imported_document(self.context, imported, role=role))
                return
            if path == "/api/reference/promote":
                self._json(200, self.app.promote_reference_document(str(payload.get("doc_id") or ""), prefetch=False))
                return
            if path == "/api/reference/remove":
                self._json(200, self.app.remove_reference_document(str(payload.get("doc_id") or "")))
                return
            if path == "/api/document/clear":
                self._json(200, self.app.clear_document())
                return
            if path == "/api/read":
                result = self.app.read_current(play=bool(payload.get("play", False)))
                self._result(409 if result.get("stale") else 200, result)
                return
            if path == "/api/next":
                self._json(200, self.app.next())
                return
            if path == "/api/previous":
                self._json(200, self.app.previous())
                return
            if path == "/api/jump":
                self._json(200, self.app.jump(int(payload.get("index", 1))))
                return
            if path == "/api/prepare/start":
                self._json(200, self.app.prepare_document(start=str(payload.get("start") or "cursor")))
                return
            if path == "/api/prepare/cancel":
                self._json(200, self.app.cancel_prepare())
                return
            if path == "/api/audio-export":
                block_value = payload.get("block")
                start_value = payload.get("start")
                end_value = payload.get("end")
                self._json(
                    200,
                    self.app.start_audio_export(
                        str(payload.get("mode") or ""),
                        block=int(block_value) if block_value is not None else None,
                        start=int(start_value) if start_value is not None else None,
                        end=int(end_value) if end_value is not None else None,
                    ),
                )
                return
            if path.startswith("/api/audio-export/cancel/"):
                self._json(200, self.app.cancel_audio_export(Path(path).name))
                return
            if path == "/api/notes/create":
                chunk_index = payload.get("chunk_index")
                self._json(
                    200,
                    self.app.create_note(
                        str(payload.get("text") or ""),
                        chunk_index=int(chunk_index) if chunk_index is not None else None,
                    ),
                )
                return
            if path == "/api/notes/update":
                self._json(
                    200,
                    self.app.update_note(
                        str(payload.get("note_id") or ""),
                        str(payload.get("text") or ""),
                        doc_id=str(payload.get("doc_id") or ""),
                    ),
                )
                return
            if path == "/api/notes/rename":
                self._json(
                    200,
                    self.app.rename_note(
                        str(payload.get("note_id") or ""),
                        str(payload.get("label") or ""),
                        doc_id=str(payload.get("doc_id") or ""),
                    ),
                )
                return
            if path == "/api/notes/delete":
                self._json(
                    200,
                    self.app.delete_note(str(payload.get("note_id") or ""), doc_id=str(payload.get("doc_id") or "")),
                )
                return
            if path == "/api/dialogue/reset":
                self._json(200, self.app.dialogue_reset())
                return
            if path == "/api/reasoning/mode":
                self._json(200, self.app.set_reasoning_mode(str(payload.get("mode") or "")))
                return
            if path == "/api/laboratory/mode":
                self._json(200, self.app.set_laboratory_mode(str(payload.get("mode") or "")))
                return
            if path == "/api/profile":
                self._json(200, self.app.set_profile(str(payload.get("mode") or "")))
                return
            if path == "/api/veil":
                self._json(200, self.app.set_veil(str(payload.get("mode") or "")))
                return
            if path == "/api/voice":
                self._json(200, self.app.set_voice(str(payload.get("voice") or "")))
                return
            if path in ("/api/laboratory/reset", "/api/chat/reset"):
                self._json(200, self.app.clear_laboratory_history())
                return
            if path == "/api/dialogue/turn":
                content_type = self.headers.get("Content-Type", "") or ""
                if "application/json" in content_type:
                    dialogue_chunk_value = payload.get("chunk_index")
                    self._result(
                        200,
                        self.app.dialogue_turn_text(
                            str(payload.get("text") or ""),
                            model=str(payload.get("model") or ""),
                            chunk_index=int(dialogue_chunk_value) if dialogue_chunk_value is not None else None,
                        ),
                    )
                    return
            if path == "/api/voice/test":
                self._result(
                    200,
                    self.app.test_voice(
                        str(payload.get("text") or "Prueba de voz neural del lector conversacional."),
                        play=bool(payload.get("play", False)),
                    ),
                )
                return
            if path == "/api/chat":
                chat_chunk_value = payload.get("chunk_index")
                self._result(
                    200,
                    self.app.chat(
                        str(payload.get("message") or ""),
                        model=str(payload.get("model") or ""),
                        chunk_index=int(chat_chunk_value) if chat_chunk_value is not None else None,
                    ),
                )
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
