#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = os.environ.get("FUSION_READER_STT_HOST", "127.0.0.1")
PORT = int(os.environ.get("FUSION_READER_STT_PORT", "8021"))
MODEL_NAME = os.environ.get("FUSION_READER_STT_MODEL", "small")
DEVICE = os.environ.get("FUSION_READER_STT_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("FUSION_READER_STT_COMPUTE_TYPE", "float16")
LANGUAGE = os.environ.get("FUSION_READER_STT_LANGUAGE", "es")
BEAM_SIZE = int(os.environ.get("FUSION_READER_STT_BEAM_SIZE", "1"))
RECOVERY_BEAM_SIZE = int(os.environ.get("FUSION_READER_STT_RECOVERY_BEAM_SIZE", str(max(2, BEAM_SIZE))))


def load_model():
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    return model, int((time.perf_counter() - started) * 1000)


MODEL, LOAD_MS = load_model()


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
    return ".audio"


def convert_to_wav(source: Path, target: Path) -> tuple[bool, str, int]:
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
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=30)
    elapsed = int((time.perf_counter() - started) * 1000)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "ffmpeg_failed").strip(), elapsed
    return True, "", elapsed


def _join_segments(segments) -> str:
    parts = []
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def transcribe_wav(path: Path, language: str) -> tuple[str, int, dict]:
    started = time.perf_counter()
    attempts: list[dict] = []
    plan = [
        {"label": "fast", "beam_size": max(1, BEAM_SIZE), "vad_filter": False},
        {"label": "recovery_vad", "beam_size": max(1, RECOVERY_BEAM_SIZE), "vad_filter": True},
    ]
    seen: set[tuple[int, bool]] = set()
    for config in plan:
        key = (int(config["beam_size"]), bool(config["vad_filter"]))
        if key in seen:
            continue
        seen.add(key)
        attempt_started = time.perf_counter()
        segments, _info = MODEL.transcribe(
            str(path),
            language=language or LANGUAGE,
            beam_size=int(config["beam_size"]),
            vad_filter=bool(config["vad_filter"]),
            condition_on_previous_text=False,
        )
        text = _join_segments(segments)
        attempts.append(
            {
                "label": str(config["label"]),
                "beam_size": int(config["beam_size"]),
                "vad_filter": bool(config["vad_filter"]),
                "decode_ms": int((time.perf_counter() - attempt_started) * 1000),
                "text_len": len(text),
            }
        )
        if text:
            total_ms = int((time.perf_counter() - started) * 1000)
            return text, total_ms, {"attempts": attempts, "selected": attempts[-1]}
    total_ms = int((time.perf_counter() - started) * 1000)
    return "", total_ms, {"attempts": attempts, "selected": {}}


class Handler(BaseHTTPRequestHandler):
    server_version = "FusionReaderV2STT/0.1"

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
                },
            )
            return
        send_json(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/transcribe":
            send_json(self, 404, {"ok": False, "error": "not_found"})
            return
        started = time.perf_counter()
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            send_json(self, 400, {"ok": False, "error": "empty_audio"})
            return
        params = parse_qs(parsed.query)
        language = str((params.get("language") or [LANGUAGE])[0] or LANGUAGE)
        mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
        with tempfile.TemporaryDirectory(prefix="fusion_reader_v2_stt_server_") as tmp:
            root = Path(tmp)
            source = root / f"input{suffix_for_mime(mime)}"
            wav = root / "normalized.wav"
            source.write_bytes(self.rfile.read(length))
            ok, detail, convert_ms = convert_to_wav(source, wav)
            if not ok:
                print(
                    "STT convert_failed "
                    f"mime={mime or 'application/octet-stream'} "
                    f"bytes={length} convert_ms={convert_ms} detail={detail}",
                    flush=True,
                )
                send_json(
                    self, 400, {"ok": False, "error": "convert_failed", "detail": detail, "convert_ms": convert_ms}
                )
                return
            try:
                text, decode_ms, decode_meta = transcribe_wav(wav, language)
            except Exception as exc:
                send_json(self, 500, {"ok": False, "error": "transcribe_failed", "detail": str(exc)})
                return
        duration_ms = int((time.perf_counter() - started) * 1000)
        selected = decode_meta.get("selected") if isinstance(decode_meta, dict) else {}
        attempts = decode_meta.get("attempts") if isinstance(decode_meta, dict) else []
        if not text:
            print(
                "STT empty_transcript "
                f"mime={mime or 'application/octet-stream'} "
                f"bytes={length} convert_ms={convert_ms} decode_ms={decode_ms} attempts={json.dumps(attempts, ensure_ascii=False)}",
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
                "duration_ms": duration_ms,
                "detail": "" if text else "empty_transcript",
            },
        )


def main() -> None:
    print(
        f"Fusion Reader v2 STT listening on http://{HOST}:{PORT} "
        f"model={MODEL_NAME} device={DEVICE} compute={COMPUTE_TYPE} load_ms={LOAD_MS}",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
