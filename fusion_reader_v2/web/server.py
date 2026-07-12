from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import re
import signal
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from fusion_reader_v2 import FusionReaderV2, import_document_bytes, import_document_path
from fusion_reader_v2.config import Settings, create_settings
from fusion_reader_v2.pdf_to_docx import (
    ConversionResult,
    JobStatus,
    convert_pdf_to_docx,
    find_downloads_dir,
    safe_output_name,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
PORT = 8010
LIBRARY_ROOT = ROOT / "library"
CONVERTED_ROOT = ROOT / "runtime" / "fusion_reader_v2" / "imported_texts"
UPLOAD_ROOT = ROOT / "runtime" / "fusion_reader_v2" / "upload_jobs"
PDF_TO_WORD_ROOT = ROOT / "runtime" / "fusion_reader_v2" / "pdf_to_word"
ALLOWED_LIBRARY_SUFFIXES = {".txt", ".md"}
IMPORT_JOBS: dict[str, dict] = {}
IMPORT_JOBS_LOCK = threading.Lock()
PDF_TO_DOCX_DOWNLOADS: dict[str, dict] = {}
PDF_TO_WORD_JOBS: dict[str, JobStatus] = {}
PDF_TO_DOCX_LOCK = threading.Lock()
APP: FusionReaderV2 | None = None
SETTINGS: Settings | None = None


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
    "commit": os.environ.get("FUSION_READER_COMMIT", "unknown"),
    "pid": os.getpid(),
    "port": PORT,
    "cwd": "",
    "started_at": "",
    "server_file": __file__,
    "python": "",
    "log_file": "",
}


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

def library_items() -> list[dict]:
    if not LIBRARY_ROOT.exists():
        return []
    items: list[dict] = []
    for path in sorted(LIBRARY_ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
            continue
        rel = path.relative_to(LIBRARY_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            text = ""
        preview = " ".join(text.split())[:170]
        items.append({
            "id": rel,
            "title": path.name,
            "bytes": path.stat().st_size,
            "preview": preview,
        })
    return items


def resolve_library_path(book_id: str) -> Path:
    raw = unquote(str(book_id or "")).strip()
    rel = Path(raw)
    if not raw or rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError("invalid_book_id")
    path = (LIBRARY_ROOT / rel).resolve()
    library_root = LIBRARY_ROOT.resolve()
    if path != library_root and library_root not in path.parents:
        raise ValueError("book_outside_library")
    if path.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
        raise ValueError("unsupported_book_type")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("book_not_found")
    return path


def audio_url_for(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve()
    cache_root = APP.cache.root.resolve()
    if path.parent != cache_root or not path.exists():
        return ""
    return f"/audio/{path.name}"


def cached_audio_path(url_path: str) -> Path | None:
    filename = Path(unquote(url_path.removeprefix("/audio/"))).name
    audio_path = (APP.cache.root / filename).resolve()
    cache_root = APP.cache.root.resolve()
    if audio_path.parent != cache_root or not audio_path.exists():
        return None
    return audio_path


def load_imported_document(imported, role: str = "main") -> dict:
    CONVERTED_ROOT.mkdir(parents=True, exist_ok=True)
    target = CONVERTED_ROOT / f"{imported.doc_id}.txt"
    target.write_text(imported.text, encoding="utf-8")
    raw_target = None
    if getattr(imported, "raw_text", ""):
        raw_target = CONVERTED_ROOT / f"{imported.doc_id}.raw.txt"
        raw_target.write_text(imported.raw_text, encoding="utf-8")
    if str(role or "main") == "reference":
        out = APP.add_reference_text(imported.doc_id, imported.title, imported.text, source_path=str(target), source_type=imported.source_type)
    else:
        out = APP.load_text(imported.doc_id, imported.title, imported.text, prefetch=False, source_path=str(target), source_type=imported.source_type)
    out["role"] = "reference" if str(role or "") == "reference" else "main"
    out["source_type"] = imported.source_type
    out["import_detail"] = imported.detail
    out["converted_text"] = str(target)
    out["raw_text"] = str(raw_target) if raw_target else ""
    out["converted_bytes"] = target.stat().st_size
    return out


def new_import_job(filename: str, mime: str, upload_path: Path, size_bytes: int, role: str = "main") -> dict:
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
    with IMPORT_JOBS_LOCK:
        IMPORT_JOBS[job_id] = job
        prune_import_jobs_locked()
    return dict(job)


def prune_import_jobs_locked(max_age_seconds: int = 6 * 60 * 60) -> None:
    now = time.time()
    stale = [
        job_id
        for job_id, job in IMPORT_JOBS.items()
        if now - float(job.get("updated_ts") or job.get("created_ts") or now) > max_age_seconds
        and str(job.get("status")) in {"done", "error"}
    ]
    for job_id in stale:
        IMPORT_JOBS.pop(job_id, None)


def update_import_job(job_id: str, **changes) -> None:
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if not job:
            return
        job.update(changes)
        current = int(job.get("current") or 0)
        total = int(job.get("total") or 0)
        if total > 0:
            job["percent"] = max(0, min(100, int(current * 100 / total)))
        job["updated_ts"] = time.time()


def import_progress_for(job_id: str):
    def progress(stage: str, current: int = 0, total: int = 0, message: str = "") -> None:
        update_import_job(job_id, status="running", stage=stage, current=int(current or 0), total=int(total or 0), message=message or stage)

    return progress


def import_job_worker(job_id: str, filename: str, upload_path: Path, mime: str, role: str = "main") -> None:
    update_import_job(job_id, status="running", stage="starting", message="Preparando conversión...")
    try:
        imported = import_document_path(filename, upload_path, mime=mime, progress=import_progress_for(job_id))
        update_import_job(job_id, status="running", stage="loading", current=0, total=0, message="Cargando texto convertido en el lector...")
        result = load_imported_document(imported, role=role)
        update_import_job(
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
        update_import_job(job_id, status="error", stage="error", message=f"No pude convertir el documento: {exc}", error=str(exc))
    finally:
        upload_path.unlink(missing_ok=True)


def get_import_job(job_id: str) -> dict | None:
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        return dict(job) if job else None


def prune_pdf_to_docx_locked(max_age_seconds: int = 6 * 60 * 60) -> None:
    now = time.time()
    stale = [
        job_id
        for job_id, job in PDF_TO_DOCX_DOWNLOADS.items()
        if now - float(job.get("created_ts") or now) > max_age_seconds
    ]
    for job_id in stale:
        PDF_TO_DOCX_DOWNLOADS.pop(job_id, None)


def register_pdf_to_docx_download(saved_path: Path, filename: str, result: ConversionResult) -> dict:
    job_id = uuid.uuid4().hex[:16]
    with PDF_TO_DOCX_LOCK:
        PDF_TO_DOCX_DOWNLOADS[job_id] = {
            "id": job_id,
            "path": str(saved_path),
            "filename": filename,
            "created_ts": time.time(),
            "pages": result.pages,
            "warnings": list(result.warnings),
        }
        prune_pdf_to_docx_locked()
    return dict(PDF_TO_DOCX_DOWNLOADS[job_id])


def get_pdf_to_docx_download(job_id: str) -> dict | None:
    with PDF_TO_DOCX_LOCK:
        item = PDF_TO_DOCX_DOWNLOADS.get(job_id)
        return dict(item) if item else None


def unique_download_target(filename: str) -> Path:
    downloads_dir = find_downloads_dir()
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
            out["audio_url"] = audio_url_for(str(out.get("audio") or ""))
        self._json(status, out)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        limit = SETTINGS.limits.upload_max_bytes if SETTINGS else 128 * 1024 * 1024
        if length > limit:
            raise ValueError("request_body_too_large")
        content_type = (self.headers.get("Content-Type", "") or "").lower()
        if "application/json" not in content_type:
            raise ValueError("application_json_required")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_json") from exc

    def _read_body_to_temp(self, filename: str) -> Path:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("missing_file_data")
        suffix = Path(Path(filename).name).suffix
        limit = SETTINGS.limits.upload_max_bytes if SETTINGS else 128 * 1024 * 1024
        if suffix.lower() == ".pdf" and SETTINGS:
            limit = SETTINGS.limits.pdf_max_bytes
        if length > limit:
            raise ValueError("upload_too_large")
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="fusion_reader_upload_", suffix=suffix, dir=UPLOAD_ROOT)
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

    def _read_multipart_file(self, field_name: str = "file", max_bytes: int = 500 * 1024 * 1024) -> tuple[str, str, bytes]:
        content_type = self.headers.get("Content-Type", "") or ""
        match = re.search(r'boundary="?([^";]+)"?', content_type)
        if "multipart/form-data" not in content_type or not match:
            raise ValueError("multipart_required")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("missing_file_data")
        if length > max_bytes:
            raise ValueError(f"PDF demasiado grande para esta versión. Límite: {max_bytes // (1024 * 1024)} MB.")
        boundary = match.group(1).encode("utf-8")
        body = self.rfile.read(length)
        marker = b"--" + boundary
        for part in body.split(marker):
            if not part or part in {b"--", b"--\r\n"}:
                continue
            part = part.strip(b"\r\n")
            if part.endswith(b"--"):
                part = part[:-2].rstrip(b"\r\n")
            header_blob, sep, data = part.partition(b"\r\n\r\n")
            if not sep:
                continue
            headers_text = header_blob.decode("utf-8", errors="replace")
            disposition = next((line for line in headers_text.split("\r\n") if line.lower().startswith("content-disposition:")), "")
            if f'name="{field_name}"' not in disposition:
                continue
            filename_match = re.search(r'filename="([^"]+)"', disposition)
            filename = Path(filename_match.group(1)).name if filename_match else "documento.pdf"
            mime_match = re.search(r"^Content-Type:\s*([^\r\n]+)", headers_text, flags=re.IGNORECASE | re.MULTILINE)
            mime = str(mime_match.group(1)).strip() if mime_match else "application/pdf"
            return filename, mime, data.rstrip(b"\r\n")
        raise ValueError("missing_file_field")

    def _remote_mutation_authorized(self) -> bool:
        if SETTINGS is None or not SETTINGS.security.allow_remote:
            return True
        expected = SETTINGS.security.api_token.encode("utf-8")
        authorization = self.headers.get("Authorization", "")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not supplied:
            supplied = self.headers.get("X-Fusion-Token", "").strip()
        return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
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
            if APP is None:
                self._json(503, {"ok": False, "error": "reader_not_composed"})
                return
            status = APP.status()
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
            st = APP.status()
            st["runtime"] = RUNTIME_INFO
            self._json(200, st)
            return
        if path == "/api/build":
            self._json(200, {"ok": True, **RUNTIME_INFO})
            return
        if path == "/api/library":
            self._json(200, {"ok": True, "items": library_items()})
            return
        if path == "/api/voice/voices" or path == "/api/voices":
            self._json(200, APP.get_voice_catalog())
            return
        if path == "/api/voice/metrics":
            self._json(200, APP.recent_voice_metrics())
            return
        if path == "/api/voice/metrics/summary":
            self._json(200, APP.voice_metrics_summary())
            return
        if path == "/api/voice/metrics/documents":
            self._json(200, APP.voice_metrics_by_document())
            return
        if path == "/api/voice/metrics/chunks":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            doc_id = str((params.get("doc_id") or [""])[0])
            limit = int((params.get("limit") or ["20"])[0])
            self._json(200, APP.voice_metrics_by_chunk(doc_id=doc_id, limit=limit))
            return
        if path == "/api/prepare/status":
            self._json(200, APP.prepare_status())
            return
        if path == "/api/audio-export/status":
            self._json(200, APP.audio_export_overview())
            return
        if path == "/api/references":
            self._json(200, APP.list_reference_documents())
            return
        if path == "/api/notes":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            doc_id = str((params.get("doc_id") or [""])[0])
            current_only = str((params.get("current_only") or ["0"])[0]).lower() in {"1", "true", "yes"}
            chunk_index_raw = str((params.get("chunk_index") or [""])[0])
            chunk_index = int(chunk_index_raw) if chunk_index_raw else None
            self._json(200, APP.list_notes(doc_id=doc_id, chunk_index=chunk_index, current_only=current_only))
            return
        if path == "/api/dialogue/status":
            self._json(200, APP.dialogue_status())
            return
        if path == "/api/import-status":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            job_id = str((params.get("id") or [""])[0])
            job = get_import_job(job_id)
            if not job:
                self._json(404, {"ok": False, "error": "import_job_not_found"})
                return
            self._json(200, job)
            return
        if path.startswith("/api/tools/pdf-to-docx/status/"):
            job_id = path.split("/")[-1]
            with PDF_TO_DOCX_LOCK:
                job = PDF_TO_WORD_JOBS.get(job_id)
            if not job:
                self._json(404, {"ok": False, "error": "Job no encontrado."})
                return
            self._json(200, {
                "ok": True,
                "job_id": job.job_id,
                "state": job.state,
                "stage": job.stage,
                "current_page": job.current_page,
                "total_pages": job.total_pages,
                "percent": job.percent,
                "message": job.message,
                "filename": job.filename,
                "saved_path": job.saved_path,
                "download_url": job.download_url,
                "warnings": job.warnings,
                "error": job.error,
                "noise_lines_removed": job.result.noise_lines_removed if job.result else 0,
                "paragraphs_merged": job.result.paragraphs_merged if job.result else 0,
                "headings_detected": job.result.headings_detected if job.result else 0,
            })
            return

        if path.startswith("/api/tools/pdf-to-docx/download/"):
            job_id = Path(path).name
            item = get_pdf_to_docx_download(job_id)
            if not item:
                self._json(404, {"ok": False, "error": "pdf_to_docx_download_not_found"})
                return
            docx_path = Path(str(item.get("path") or "")).resolve()
            if not docx_path.exists():
                self._json(404, {"ok": False, "error": "pdf_to_docx_file_missing"})
                return
            raw = docx_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Disposition", f"attachment; filename=\"{Path(str(item.get('filename') or 'documento.docx')).name}\"")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path.startswith("/api/audio-export/status/"):
            job_id = Path(path).name
            status = APP.audio_export_status(job_id)
            self._json(200 if status.get("ok") else 404, status)
            return
        if path.startswith("/api/audio-export/download/"):
            job_id = Path(path).name
            item = APP.get_audio_export_download(job_id)
            if not item.get("ok"):
                self._json(404, item)
                return
            wav_path = Path(str(item.get("path") or "")).resolve()
            raw = wav_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Disposition", f"attachment; filename=\"{Path(str(item.get('filename') or 'audio.wav')).name}\"")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path.startswith("/audio/"):
            audio_path = cached_audio_path(path)
            if not audio_path:
                self._json(404, {"ok": False, "error": "audio_not_found"})
                return
            self._send(200, "audio/wav", audio_path.read_bytes())
            return
        self._json(404, {"ok": False, "error": "not_found"})

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
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
            st = APP.status()
            st["runtime"] = RUNTIME_INFO
            raw = json.dumps(st).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return
        if path.startswith("/audio/"):
            audio_path = cached_audio_path(path)
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
        if not self._remote_mutation_authorized():
            self._json(401, {"ok": False, "error": "api_token_required"})
            return
        try:
            if path == "/api/tools/pdf-to-docx":
                filename, mime, raw = self._read_multipart_file(field_name="file")
                clean_name = Path(filename).name
                if Path(clean_name).suffix.lower() != ".pdf":
                    self._json(400, {"ok": False, "error": "Solo se aceptan archivos PDF."})
                    return
                PDF_TO_WORD_ROOT.mkdir(parents=True, exist_ok=True)
                job_id = uuid.uuid4().hex[:16]
                input_path = PDF_TO_WORD_ROOT / f"{job_id}.pdf"
                input_path.write_bytes(raw)

                job = JobStatus(job_id=job_id, filename=safe_output_name(clean_name))
                with PDF_TO_DOCX_LOCK:
                    PDF_TO_WORD_JOBS[job_id] = job

                def _run_job(j: JobStatus, in_p: Path, original_name: str):
                    try:
                        temp_docx = PDF_TO_WORD_ROOT / f"{j.job_id}.docx"
                        result = convert_pdf_to_docx(in_p, temp_docx, job=j)
                        if not result.ok:
                            j.state = "error"
                            j.error = result.error or "Error desconocido en conversión."
                            return

                        if j.cancelled:
                            j.state = "cancelled"
                            return

                        final_target = unique_download_target(j.filename)
                        final_target.write_bytes(temp_docx.read_bytes())
                        download_item = register_pdf_to_docx_download(final_target, final_target.name, result)

                        j.state = "done"
                        j.saved_path = str(final_target)
                        j.filename = final_target.name
                        j.download_url = f"/api/tools/pdf-to-docx/download/{download_item['id']}"
                        j.warnings = list(result.warnings)
                        temp_docx.unlink(missing_ok=True)
                    except Exception as e:
                        j.state = "error"
                        j.error = str(e)
                    finally:
                        in_p.unlink(missing_ok=True)

                threading.Thread(target=_run_job, args=(job, input_path, clean_name), daemon=True).start()
                self._json(200, {"ok": True, "job_id": job_id})
                return

            if path.startswith("/api/tools/pdf-to-docx/cancel/"):
                job_id = path.split("/")[-1]
                with PDF_TO_DOCX_LOCK:
                    job = PDF_TO_WORD_JOBS.get(job_id)
                if not job:
                    self._json(404, {"ok": False, "error": "Job no encontrado."})
                    return
                job.cancelled = True
                job.state = "cancelled"
                self._json(200, {"ok": True, "job_id": job_id})
                return
            if path == "/api/import-file/start":
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["documento"])[0])
                mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
                role = str((params.get("role") or ["main"])[0])
                tmp_path = self._read_body_to_temp(filename)
                job = new_import_job(filename, mime, tmp_path, tmp_path.stat().st_size, role=role)
                thread = threading.Thread(
                    target=import_job_worker,
                    args=(str(job["job_id"]), filename, tmp_path, mime, role),
                    name=f"fusion-import-{job['job_id']}",
                    daemon=True,
                )
                thread.start()
                self._json(202, job)
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
                self._json(200, load_imported_document(imported, role=role))
                return
            if path == "/api/dialogue/turn" and "application/json" not in (self.headers.get("Content-Type", "") or ""):
                content_type = self.headers.get("Content-Type", "") or ""
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["dialogue.webm"])[0])
                raw_chunk_index = (params.get("chunk_index") or [None])[0]
                chunk_index = int(raw_chunk_index) if raw_chunk_index not in (None, "") else None
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
                    self._result(200, APP.dialogue_turn_audio(tmp_path, mime=content_type, model=str((params.get("model") or [""])[0]), chunk_index=chunk_index, audio_meta=audio_meta))
                finally:
                    tmp_path.unlink(missing_ok=True)
                return
            payload = self._payload()
            if path == "/api/load":
                role = str(payload.get("role") or "main")
                if payload.get("book_id"):
                    if role == "reference":
                        self._json(200, APP.add_reference_file(resolve_library_path(str(payload.get("book_id")))))
                    else:
                        self._json(200, APP.load_file(resolve_library_path(str(payload.get("book_id"))), prefetch=False))
                    return
                if payload.get("text"):
                    if role == "reference":
                        self._json(
                            200,
                            APP.add_reference_text(
                                str(payload.get("doc_id") or "manual"),
                                str(payload.get("title") or "Manual"),
                                str(payload.get("text")),
                                source_type="manual",
                            ),
                        )
                    else:
                        self._json(
                            200,
                            APP.load_text(
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
                        self._json(200, APP.add_reference_file(resolve_library_path(str(payload.get("path")))))
                    else:
                        self._json(200, APP.load_file(resolve_library_path(str(payload.get("path"))), prefetch=False))
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
                imported = import_document_bytes(filename, base64.b64decode(raw_b64), mime=mime)
                self._json(200, load_imported_document(imported, role=role))
                return
            if path == "/api/reference/promote":
                self._json(200, APP.promote_reference_document(str(payload.get("doc_id") or ""), prefetch=False))
                return
            if path == "/api/reference/remove":
                self._json(200, APP.remove_reference_document(str(payload.get("doc_id") or "")))
                return
            if path == "/api/document/clear":
                self._json(200, APP.clear_document())
                return
            if path == "/api/read":
                result = APP.read_current(play=bool(payload.get("play", False)))
                self._result(409 if result.get("stale") else 200, result)
                return
            if path == "/api/next":
                self._json(200, APP.next())
                return
            if path == "/api/previous":
                self._json(200, APP.previous())
                return
            if path == "/api/jump":
                self._json(200, APP.jump(int(payload.get("index", 1))))
                return
            if path == "/api/prepare/start":
                self._json(200, APP.prepare_document(start=str(payload.get("start") or "cursor")))
                return
            if path == "/api/prepare/cancel":
                self._json(200, APP.cancel_prepare())
                return
            if path == "/api/audio-export":
                self._json(
                    200,
                    APP.start_audio_export(
                        str(payload.get("mode") or ""),
                        block=int(payload.get("block")) if payload.get("block") is not None else None,
                        start=int(payload.get("start")) if payload.get("start") is not None else None,
                        end=int(payload.get("end")) if payload.get("end") is not None else None,
                    ),
                )
                return
            if path.startswith("/api/audio-export/cancel/"):
                self._json(200, APP.cancel_audio_export(Path(path).name))
                return
            if path == "/api/notes/create":
                chunk_index = payload.get("chunk_index")
                self._json(200, APP.create_note(str(payload.get("text") or ""), chunk_index=int(chunk_index) if chunk_index is not None else None))
                return
            if path == "/api/notes/update":
                self._json(200, APP.update_note(str(payload.get("note_id") or ""), str(payload.get("text") or ""), doc_id=str(payload.get("doc_id") or "")))
                return
            if path == "/api/notes/rename":
                self._json(200, APP.rename_note(str(payload.get("note_id") or ""), str(payload.get("label") or ""), doc_id=str(payload.get("doc_id") or "")))
                return
            if path == "/api/notes/delete":
                self._json(200, APP.delete_note(str(payload.get("note_id") or ""), doc_id=str(payload.get("doc_id") or "")))
                return
            if path == "/api/dialogue/reset":
                self._json(200, APP.dialogue_reset())
                return
            if path == "/api/reasoning/mode":
                self._json(200, APP.set_reasoning_mode(str(payload.get("mode") or "")))
                return
            if path == "/api/laboratory/mode":
                self._json(200, APP.set_laboratory_mode(str(payload.get("mode") or "")))
                return
            if path == "/api/profile":
                self._json(200, APP.set_profile(str(payload.get("mode") or "")))
                return
            if path == "/api/veil":
                self._json(200, APP.set_veil(str(payload.get("mode") or "")))
                return
            if path == "/api/voice":
                self._json(200, APP.set_voice(str(payload.get("voice") or "")))
                return
            if path in ("/api/laboratory/reset", "/api/chat/reset"):
                self._json(200, APP.clear_laboratory_history())
                return
            if path == "/api/dialogue/turn":
                content_type = self.headers.get("Content-Type", "") or ""
                if "application/json" in content_type:
                    raw_chunk_index = payload.get("chunk_index")
                    self._result(200, APP.dialogue_turn_text(str(payload.get("text") or ""), model=str(payload.get("model") or ""), chunk_index=int(raw_chunk_index) if raw_chunk_index is not None else None))
                    return
            if path == "/api/voice/test":
                self._result(200, APP.test_voice(str(payload.get("text") or "Prueba de voz neural del lector conversacional."), play=bool(payload.get("play", False))))
                return
            if path == "/api/chat":
                raw_chunk_index = payload.get("chunk_index")
                self._result(200, APP.chat(str(payload.get("message") or ""), model=str(payload.get("model") or ""), chunk_index=int(raw_chunk_index) if raw_chunk_index is not None else None))
                return
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(404, {"ok": False, "error": "not_found"})




def create_http_server(app: FusionReaderV2, settings: Settings) -> ThreadingHTTPServer:
    global APP, SETTINGS, PORT, LIBRARY_ROOT, CONVERTED_ROOT, UPLOAD_ROOT, PDF_TO_WORD_ROOT, RUNTIME_INFO
    APP = app
    SETTINGS = settings
    PORT = settings.ports.api
    LIBRARY_ROOT = settings.paths.library
    CONVERTED_ROOT = settings.paths.runtime / "imported_texts"
    UPLOAD_ROOT = settings.paths.runtime / "upload_jobs"
    PDF_TO_WORD_ROOT = settings.paths.runtime / "pdf_to_word"
    RUNTIME_INFO = {
        "app": "fusion_reader_v2",
        "commit": _get_git_commit(settings.paths.repository),
        "pid": os.getpid(),
        "port": settings.ports.api,
        "cwd": str(settings.paths.repository),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "server_file": __file__,
        "python": os.sys.executable,
        "log_file": str(settings.paths.logs / "fusion_reader_v2_server.log"),
    }
    return FusionHTTPServer((settings.security.bind_host, settings.ports.api), Handler)


def main() -> None:
    from fusion_reader_v2.composition import create_fusion_reader

    settings = create_settings()
    app = create_fusion_reader(settings)
    server = create_http_server(app, settings)

    def request_shutdown(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, name="fusion-http-shutdown").start()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    print(
        f"Fusion Reader v2 API listening on "
        f"http://{settings.security.bind_host}:{settings.ports.api}"
    )
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
