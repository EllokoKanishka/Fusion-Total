"""Local word timings for existing reader audio; never synthesizes speech."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import socket
import tempfile
import threading
import unicodedata
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode, urlsplit


def _normalize(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word.casefold()) if c.isalnum())


def align_word_times(text: str, segments: list[dict]) -> dict:
    """Map measured ASR words to original text, keeping original spelling intact.

    Unrecognized words receive no invented timestamps. Cumulative highlighting
    includes them only when the next recognized source word is reached.
    Offsets use UTF-16 code units, as required by browser String.slice.
    """
    source = list(re.finditer(r"\w+(?:['’]\w+)*", text))
    measured: list[tuple[str, float, float]] = []
    for segment in segments:
        for word in segment.get("words") or []:
            try:
                start, end = float(word["start"]), float(word["end"])
                token = _normalize(str(word["word"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not token or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                continue
            if measured and start < measured[-1][1]:
                continue
            measured.append((token, start, end))
    matcher = SequenceMatcher(None, [_normalize(m.group()) for m in source], [w[0] for w in measured], autojunk=False)
    words = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            match = source[block.a + offset]
            _, start, end = measured[block.b + offset]
            words.append(
                {
                    "start": start,
                    "end": end,
                    "start_offset": len(text[: match.start()].encode("utf-16-le")) // 2,
                    "end_offset": len(text[: match.end()].encode("utf-16-le")) // 2,
                }
            )
    coverage = len(words) / max(1, len(source))
    if not words or coverage < 0.7:
        return {"ok": False, "state": "unavailable", "detail": "insufficient_word_alignment", "coverage": coverage}
    return {"ok": True, "state": "ready", "words": words, "coverage": round(coverage, 3)}


class ReadingTimings:
    """One owned background request, bounded disk cache, local STT only."""

    def __init__(self, *, root: Path, stt_url: str, spawn, timeout: float = 120.0) -> None:
        self.root = root
        self.url = urlsplit(stt_url)
        self.spawn = spawn
        self.timeout = timeout
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.jobs: dict[str, dict] = {}
        self.active = ""
        self.connection: http.client.HTTPConnection | None = None
        self.request_id = ""

    def start(self, audio: Path, text: str, language: str, *, retry: bool = False) -> dict:
        if self.url.scheme != "http" or self.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return {"ok": False, "state": "unavailable", "detail": "local_word_timing_provider_required"}
        if not text or len(text) > 20000 or audio.stat().st_size > 64 * 1024 * 1024:
            return {"ok": False, "state": "unavailable", "detail": "alignment_limit"}
        digest = hashlib.sha256(("words-v1\0" + language + "\0" + text).encode())
        with audio.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        key = digest.hexdigest()
        with self.lock:
            if self.closed.is_set():
                return {"ok": False, "state": "unavailable", "detail": "closed"}
            if key in self.jobs:
                if retry and not self.jobs[key].get("ok"):
                    self.jobs.pop(key)
                else:
                    return dict(self.jobs[key])
            path = self.root / f"{key}.json"
            try:
                if path.stat().st_size <= 1024 * 1024:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                    if cached.get("state") == "ready" and isinstance(cached.get("words"), list):
                        return {**cached, "cached": True}
            except (OSError, ValueError, AttributeError):
                pass
            if self.active:
                return {"ok": True, "state": "waiting"}
            if len(self.jobs) >= 64:
                self.jobs.pop(next(iter(self.jobs)))
            self.active = key
            self.jobs[key] = {"ok": True, "state": "pending"}
            try:
                self.spawn(target=self._run, args=(key, audio, text, language), name="fusion-word-timings")
            except RuntimeError:
                self.active = ""
                self.jobs.pop(key, None)
                return {"ok": False, "state": "unavailable", "detail": "closed"}
            return dict(self.jobs[key])

    def _run(self, key: str, audio: Path, text: str, language: str) -> None:
        connection = http.client.HTTPConnection(
            self.url.hostname or "127.0.0.1", self.url.port or 80, timeout=self.timeout
        )
        request_id = f"reader-words-{uuid.uuid4().hex}"
        result: dict = {"ok": False, "state": "unavailable", "detail": "word_timings_unavailable"}
        try:
            with self.lock:
                if self.closed.is_set():
                    return
                self.connection = connection
                self.request_id = request_id
            query = urlencode(
                {"language": language, "mime": "audio/wav", "word_timestamps": "1", "request_id": request_id}
            )
            connection.putrequest("POST", f"{self.url.path.rstrip('/')}/transcribe?{query}")
            connection.putheader("Content-Type", "audio/wav")
            connection.putheader("Content-Length", str(audio.stat().st_size))
            connection.endheaders()
            with audio.open("rb") as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    if self.closed.is_set():
                        return
                    connection.send(chunk)
            response = connection.getresponse()
            raw = response.read(4 * 1024 * 1024 + 1)
            if response.status == 200 and len(raw) <= 4 * 1024 * 1024:
                data = json.loads(raw)
                if data.get("ok") and isinstance(data.get("segments"), list):
                    result = align_word_times(text, data["segments"])
            if result.get("ok") and not self.closed.is_set():
                self._save(key, result)
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            pass
        finally:
            connection.close()
            with self.lock:
                self.jobs[key] = result
                self.connection = None
                self.request_id = ""
                self.active = ""

    def _save(self, key: str, result: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root, suffix=".tmp", delete=False
            ) as f:
                temporary = Path(f.name)
                json.dump(result, f, separators=(",", ":"))
            os.replace(temporary, self.root / f"{key}.json")
            temporary = None
            entries = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for old in entries[:-128]:
                old.unlink(missing_ok=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def request_shutdown(self) -> None:
        self.closed.set()
        with self.lock:
            connection, request_id = self.connection, self.request_id
        if connection is not None:
            try:
                if connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if request_id:
            cancel = http.client.HTTPConnection(self.url.hostname or "127.0.0.1", self.url.port or 80, timeout=1.0)
            try:
                cancel.request("POST", f"{self.url.path.rstrip('/')}/cancel/{request_id}", body=b"")
                cancel.getresponse().read()
            except (OSError, http.client.HTTPException):
                pass
            finally:
                cancel.close()
