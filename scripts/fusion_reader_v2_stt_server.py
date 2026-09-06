#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fusion_reader_v2.owned_subprocess import run_owned  # noqa: E402


HOST = os.environ.get("FUSION_READER_STT_HOST", "127.0.0.1")
PORT = int(os.environ.get("FUSION_READER_STT_PORT", "8021"))
MODEL_NAME = os.environ.get("FUSION_READER_STT_MODEL", "large-v3-turbo")
DEVICE = os.environ.get("FUSION_READER_STT_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("FUSION_READER_STT_COMPUTE_TYPE", "float16")
LANGUAGE = os.environ.get("FUSION_READER_STT_LANGUAGE", "es")
BEAM_SIZE = int(os.environ.get("FUSION_READER_STT_BEAM_SIZE", "5"))
RECOVERY_BEAM_SIZE = int(os.environ.get("FUSION_READER_STT_RECOVERY_BEAM_SIZE", str(max(5, BEAM_SIZE))))
CONVERT_TIMEOUT_SECONDS = float(os.environ.get("FUSION_READER_STT_CONVERT_TIMEOUT_SECONDS", "1800"))
MAX_BODY_BYTES = int(os.environ.get("FUSION_READER_STT_MAX_BODY_BYTES", str(2 * 1024 * 1024 * 1024)))
SOCKET_TIMEOUT_SECONDS = float(os.environ.get("FUSION_READER_STT_SOCKET_TIMEOUT_SECONDS", "60"))
CONTEXT_MAX_CHARS = int(os.environ.get("FUSION_READER_STT_CONTEXT_MAX_CHARS", "4096"))
HOTWORD_MAX_ITEMS = int(os.environ.get("FUSION_READER_STT_HOTWORD_MAX_ITEMS", "128"))
INFERENCE_LOCK = threading.Lock()
REQUESTS_LOCK = threading.Lock()
ACTIVE_REQUESTS: dict[str, threading.Event] = {}


def _bounded_text(value: str | None, max_chars: int = CONTEXT_MAX_CHARS) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    return normalized[: max(0, max_chars)]


def _normalize_hotwords(value: str | None) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\n]+", str(value or "")):
        clean = _bounded_text(item, 160)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        unique.append(clean)
        if len(unique) >= max(1, HOTWORD_MAX_ITEMS):
            break
    return ", ".join(unique)[: max(0, CONTEXT_MAX_CHARS)]


def _load_hotwords() -> str:
    raw_items: list[str] = []
    direct = os.environ.get("FUSION_READER_STT_HOTWORDS", "")
    if direct:
        raw_items.append(direct)

    hotwords_file = str(os.environ.get("FUSION_READER_STT_HOTWORDS_FILE", "") or "").strip()
    if hotwords_file:
        path = Path(hotwords_file).expanduser()
        try:
            raw_items.extend(
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
        except OSError as exc:
            print(f"STT hotwords file unavailable: {path} ({exc})", file=sys.stderr, flush=True)

    return _normalize_hotwords("\n".join(raw_items))


INITIAL_PROMPT = _bounded_text(os.environ.get("FUSION_READER_STT_INITIAL_PROMPT", ""))
HOTWORDS = _load_hotwords()


def load_model():
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    return model, int((time.perf_counter() - started) * 1000)


MODEL, LOAD_MS = load_model()
try:
    transcribe_method = getattr(MODEL, "transcribe")
    TRANSCRIBE_PARAMETERS: set[str] | None = set(inspect.signature(transcribe_method).parameters)
except (AttributeError, TypeError, ValueError):
    TRANSCRIBE_PARAMETERS = None


def _context_options(initial_prompt: str = "", hotwords: str = "") -> dict:
    options: dict[str, str] = {}
    prompt_parts: list[str] = []
    request_prompt = _bounded_text(initial_prompt)
    combined_hotwords = _normalize_hotwords("\n".join(item for item in (HOTWORDS, hotwords) if item))
    if INITIAL_PROMPT:
        prompt_parts.append(INITIAL_PROMPT)
    if request_prompt and request_prompt != INITIAL_PROMPT:
        prompt_parts.append(request_prompt)
    if combined_hotwords:
        if TRANSCRIBE_PARAMETERS is not None and "hotwords" in TRANSCRIBE_PARAMETERS:
            options["hotwords"] = combined_hotwords
        else:
            # Older faster-whisper versions can still receive the terms through
            # Whisper's initial prompt even when native hotword biasing is absent.
            prompt_parts.append(f"Vocabulario relevante: {combined_hotwords}")
    if prompt_parts and (TRANSCRIBE_PARAMETERS is None or "initial_prompt" in TRANSCRIBE_PARAMETERS):
        options["initial_prompt"] = _bounded_text(" ".join(prompt_parts))
    return options


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def suffix_for_mime(mime: str) -> str:
    lowered = str(mime or "").lower()
    if "webm" in lowered:
        return ".webm"
    if "ogg" in lowered:
        return ".ogg"
    if "wav" in lowered:
        return ".wav"
    if "mpeg" in lowered or "mp3" in lowered:
        return ".mp3"
    if "flac" in lowered:
        return ".flac"
    return ".audio"


def convert_to_wav(source: Path, target: Path, cancel_event=None) -> tuple[bool, str, int]:
    started = time.perf_counter()
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        str(target),
    ]
    proc = run_owned(cmd, check=False, text=True, timeout=CONVERT_TIMEOUT_SECONDS, cancel_event=cancel_event)
    elapsed = int((time.perf_counter() - started) * 1000)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "ffmpeg_failed").strip(), elapsed
    return True, "", elapsed


def _is_hallucinated_segment(text: str) -> bool:
    normalized = unicodedata.normalize("NFD", str(text or "").lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    clean = " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())
    phrases = (
        "subtitulos realizados por la comunidad de amara org",
        "subtitulos por la comunidad de amara org",
        "gracias por ver el video",
        "suscribete al canal",
        "www youtube com",
    )
    return any(clean == phrase or (len(clean.split()) <= 12 and phrase in clean) for phrase in phrases)


def _segment_payload(segments, cancel_event=None) -> tuple[str, list[dict]]:
    parts: list[str] = []
    payload: list[dict] = []
    for segment in segments:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("cancelled")
        text = str(getattr(segment, "text", "") or "").strip()
        if text and not _is_hallucinated_segment(text):
            parts.append(text)
            payload.append(
                {
                    "start": round(float(getattr(segment, "start", 0.0) or 0.0), 3),
                    "end": round(float(getattr(segment, "end", 0.0) or 0.0), 3),
                    "text": text,
                }
            )
    return " ".join(parts).strip(), payload


def transcribe_wav(
    path: Path,
    language: str,
    *,
    cancel_event=None,
    long_form: bool = False,
    initial_prompt: str = "",
    hotwords: str = "",
) -> tuple[str, int, dict]:
    started = time.perf_counter()
    attempts: list[dict] = []
    plan: list[tuple[str, int, bool]] = [
        ("quality", max(1, BEAM_SIZE), bool(long_form)),
        ("recovery_vad", max(1, RECOVERY_BEAM_SIZE), True),
        ("recovery_novad", max(1, RECOVERY_BEAM_SIZE), False),
    ]
    seen: set[tuple[int, bool]] = set()
    for label, beam_size, vad_filter in plan:
        key = (beam_size, vad_filter)
        if key in seen:
            continue
        seen.add(key)
        attempt_started = time.perf_counter()
        requested_language = str(language or "").strip().lower()
        selected_language = None if requested_language in {"", "auto", "detect"} else requested_language
        while not INFERENCE_LOCK.acquire(timeout=0.2):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("cancelled")
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("cancelled")
            segments, info = MODEL.transcribe(
                str(path),
                language=selected_language,
                beam_size=beam_size,
                vad_filter=vad_filter,
                condition_on_previous_text=bool(long_form),
                **_context_options(initial_prompt, hotwords),
            )
            text, segment_items = _segment_payload(segments, cancel_event)
        finally:
            INFERENCE_LOCK.release()
        attempts.append(
            {
                "label": label,
                "beam_size": beam_size,
                "vad_filter": vad_filter,
                "decode_ms": int((time.perf_counter() - attempt_started) * 1000),
                "text_len": len(text),
            }
        )
        if text:
            total_ms = int((time.perf_counter() - started) * 1000)
            return (
                text,
                total_ms,
                {
                    "attempts": attempts,
                    "selected": attempts[-1],
                    "detected_language": str(getattr(info, "language", "") or selected_language or ""),
                    "segments": segment_items,
                },
            )
    total_ms = int((time.perf_counter() - started) * 1000)
    return "", total_ms, {"attempts": attempts, "selected": {}}


class Handler(BaseHTTPRequestHandler):
    server_version = "FusionReaderV2STT/0.3"

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            send_json(
                self,
                200,
                {
                    "ok": True,
                    "provider": "faster_whisper_server",
                    "model": MODEL_NAME,
                    "device": DEVICE,
                    "compute_type": COMPUTE_TYPE,
                    "beam_size": BEAM_SIZE,
                    "load_ms": LOAD_MS,
                    "quality_profile": "quality" if DEVICE == "cuda" else "balanced",
                    "context_biasing": {
                        "initial_prompt": bool(INITIAL_PROMPT),
                        "hotwords": bool(HOTWORDS),
                        "native_hotwords": bool(TRANSCRIBE_PARAMETERS and "hotwords" in TRANSCRIBE_PARAMETERS),
                        "per_request": True,
                    },
                },
            )
            return
        send_json(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/cancel/"):
            request_id = parsed.path.rsplit("/", 1)[-1]
            with REQUESTS_LOCK:
                event = ACTIVE_REQUESTS.get(request_id)
                if event is not None:
                    event.set()
            send_json(self, 200, {"ok": True, "request_id": request_id, "active": event is not None})
            return
        if parsed.path != "/transcribe":
            send_json(self, 404, {"ok": False, "error": "not_found"})
            return
        started = time.perf_counter()
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            send_json(self, 400, {"ok": False, "error": "empty_audio"})
            return
        if length > MAX_BODY_BYTES:
            send_json(self, 413, {"ok": False, "error": "audio_too_large", "max_bytes": MAX_BODY_BYTES})
            return
        params = parse_qs(parsed.query)
        language = str((params.get("language") or [LANGUAGE])[0] or LANGUAGE)
        mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
        request_id = re.sub(r"[^A-Za-z0-9_-]", "", str((params.get("request_id") or [""])[0]))[:80]
        long_form = str((params.get("long_form") or ["0"])[0]).lower() in {"1", "true", "yes", "on"}
        initial_prompt = _bounded_text(str((params.get("initial_prompt") or [""])[0]))
        request_hotwords = _normalize_hotwords(str((params.get("hotwords") or [""])[0]))
        cancel_event = threading.Event()
        if request_id:
            with REQUESTS_LOCK:
                ACTIVE_REQUESTS[request_id] = cancel_event
        self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        with tempfile.TemporaryDirectory(prefix="fusion_reader_v2_stt_server_") as tmp:
            root = Path(tmp)
            source = root / f"input{suffix_for_mime(mime)}"
            wav = root / "normalized.wav"
            remaining = length
            try:
                with source.open("wb") as handle:
                    while remaining > 0:
                        if cancel_event.is_set():
                            raise RuntimeError("cancelled")
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise RuntimeError("incomplete_audio")
                        handle.write(chunk)
                        remaining -= len(chunk)
                if mime.lower().split(";", 1)[0].strip() == "audio/flac":
                    transcription_source = source
                    convert_ms = 0
                    ok, detail = True, ""
                else:
                    transcription_source = wav
                    ok, detail, convert_ms = convert_to_wav(source, wav, cancel_event)
            except Exception as exc:
                if request_id:
                    with REQUESTS_LOCK:
                        ACTIVE_REQUESTS.pop(request_id, None)
                error = "cancelled" if cancel_event.is_set() or str(exc) == "cancelled" else str(exc)
                send_json(self, 499 if error == "cancelled" else 400, {"ok": False, "error": error})
                return
            if not ok:
                if request_id:
                    with REQUESTS_LOCK:
                        ACTIVE_REQUESTS.pop(request_id, None)
                print(
                    "STT convert_failed "
                    f"mime={mime or 'application/octet-stream'} "
                    f"bytes={length} convert_ms={convert_ms} detail={detail}",
                    flush=True,
                )
                send_json(
                    self,
                    400,
                    {"ok": False, "error": "convert_failed", "detail": detail, "convert_ms": convert_ms},
                )
                return
            try:
                text, decode_ms, decode_meta = transcribe_wav(
                    transcription_source,
                    language,
                    cancel_event=cancel_event,
                    long_form=long_form,
                    initial_prompt=initial_prompt,
                    hotwords=request_hotwords,
                )
            except Exception as exc:
                error = "cancelled" if cancel_event.is_set() or str(exc) == "cancelled" else "transcribe_failed"
                send_json(
                    self,
                    499 if error == "cancelled" else 500,
                    {"ok": False, "error": error, "detail": str(exc)},
                )
                return
            finally:
                if request_id:
                    with REQUESTS_LOCK:
                        ACTIVE_REQUESTS.pop(request_id, None)
        duration_ms = int((time.perf_counter() - started) * 1000)
        selected = (decode_meta.get("selected") or {}) if isinstance(decode_meta, dict) else {}
        attempts = decode_meta.get("attempts") if isinstance(decode_meta, dict) else []
        if not text:
            print(
                "STT empty_transcript "
                f"mime={mime or 'application/octet-stream'} "
                f"bytes={length} convert_ms={convert_ms} decode_ms={decode_ms} "
                f"attempts={json.dumps(attempts, ensure_ascii=False)}",
                flush=True,
            )
        send_json(
            self,
            200,
            {
                "ok": bool(text),
                "text": text,
                "provider": "faster_whisper_server",
                "model": MODEL_NAME,
                "device": DEVICE,
                "compute_type": COMPUTE_TYPE,
                "beam_size": int(selected.get("beam_size") or BEAM_SIZE),
                "vad_filter": bool(selected.get("vad_filter")) if selected else False,
                "convert_ms": convert_ms,
                "decode_ms": decode_ms,
                "decode_attempts": attempts,
                "detected_language": str(decode_meta.get("detected_language") or language or ""),
                "segments": list(decode_meta.get("segments") or []),
                "duration_ms": duration_ms,
                "context_biasing": bool(initial_prompt or request_hotwords),
                "detail": "" if text else "empty_transcript",
            },
        )


def main() -> None:
    print(
        f"Fusion Reader v2 STT listening on http://{HOST}:{PORT} "
        f"model={MODEL_NAME} device={DEVICE} compute={COMPUTE_TYPE} beam={BEAM_SIZE} load_ms={LOAD_MS} "
        f"context_biasing={'on' if INITIAL_PROMPT or HOTWORDS else 'off'}",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
