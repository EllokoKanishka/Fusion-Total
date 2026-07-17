from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import environment_value
from .owned_subprocess import run_owned


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptResult:
    ok: bool
    text: str = ""
    provider: str = ""
    detail: str = ""
    duration_ms: int = 0
    timings: dict | None = None
    detected_language: str = ""
    segments: tuple[TranscriptSegment, ...] = ()


class STTProvider:
    name = "base"
    requested_provider = "auto"

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "detail": "not_implemented"}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(False, provider=self.name, detail="not_implemented")


_SHORT_HALLUCINATED_TRANSCRIPT_PATTERNS = [
    re.compile(r"suscribete(?: al canal)?"),
    re.compile(r"no olvides suscribirte(?: al canal)?"),
    re.compile(r"(?:dale|deja|denle) (?:un )?like"),
    re.compile(r"like y suscribete"),
    re.compile(r"activa (?:la )?campanita"),
    re.compile(r"gracias por ver(?: el video)?"),
    re.compile(r"hasta la proxima"),
    re.compile(r"giraff"),
]

_LONG_HALLUCINATED_TRANSCRIPT_PATTERNS = [
    re.compile(r"subtitulos realizados por la comunidad de amara org"),
    re.compile(r"subtitulos por la comunidad de amara org"),
    re.compile(r"amara org"),
    re.compile(r"www youtube com"),
]


def _normalize_transcript_for_filter(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or "").lower())
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split()).strip()


def is_hallucinated_transcript(text: str) -> bool:
    """Detect common Whisper outro hallucinations from silence or very short audio."""
    clean = _normalize_transcript_for_filter(text)
    if not clean:
        return False
    words = clean.split()
    if len(words) <= 8 and any(pattern.fullmatch(clean) for pattern in _SHORT_HALLUCINATED_TRANSCRIPT_PATTERNS):
        return True
    return any(pattern.fullmatch(clean) for pattern in _LONG_HALLUCINATED_TRANSCRIPT_PATTERNS)


class NullSTTProvider(STTProvider):
    name = "null_stt"

    def __init__(self, text: str = "Texto de prueba.", *, enabled: bool = True) -> None:
        self.text = text
        self.enabled = enabled
        self.calls: list[tuple[Path, str, str]] = []

    def health(self) -> dict:
        return {
            "ok": self.enabled,
            "provider": self.name,
            "enabled": self.enabled,
            "detail": "disabled" if not self.enabled else "",
        }

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        started = time.perf_counter()
        self.calls.append((Path(path), mime, language))
        return TranscriptResult(
            True, text=self.text, provider=self.name, duration_ms=int((time.perf_counter() - started) * 1000)
        )


class WhisperCliSTTProvider(STTProvider):
    name = "whisper_cli"

    def __init__(
        self,
        command: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        threads: int | None = None,
    ) -> None:
        self.command = command or environment_value("FUSION_READER_STT_COMMAND") or _default_whisper_command()
        self.model = model or environment_value("FUSION_READER_STT_MODEL") or "small"
        self.timeout_seconds = timeout_seconds or float(environment_value("FUSION_READER_STT_TIMEOUT", "180") or "180")
        self.threads = (
            threads if threads is not None else int(environment_value("FUSION_READER_STT_THREADS", "8") or "8")
        )

    def health(self) -> dict:
        resolved = shutil.which(self.command)
        if not resolved:
            return {"ok": False, "provider": self.name, "command": self.command, "detail": "command_not_found"}
        return {"ok": True, "provider": self.name, "command": resolved, "model": self.model}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        started = time.perf_counter()
        source = Path(path)
        if not source.exists() or source.stat().st_size <= 0:
            return TranscriptResult(False, provider=self.name, detail="empty_audio")
        if not shutil.which(self.command):
            return TranscriptResult(False, provider=self.name, detail="command_not_found")
        with tempfile.TemporaryDirectory(prefix="fusion_reader_v2_stt_") as tmp:
            out_dir = Path(tmp)
            cmd = [
                self.command,
                str(source),
                "--model",
                self.model,
                "--task",
                "transcribe",
                "--output_format",
                "txt",
                "--output_dir",
                str(out_dir),
                "--verbose",
                "False",
                "--fp16",
                "False",
                "--threads",
                str(max(1, self.threads)),
            ]
            requested_language = str(language or "").strip().lower()
            if requested_language not in {"", "auto", "detect"}:
                cmd[4:4] = ["--language", requested_language]
            try:
                proc = run_owned(cmd, check=False, text=True, capture_output=True, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                return TranscriptResult(
                    False, provider=self.name, detail="timeout", duration_ms=int((time.perf_counter() - started) * 1000)
                )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "whisper_failed").strip().splitlines()
                return TranscriptResult(
                    False,
                    provider=self.name,
                    detail=(detail[-1] if detail else "whisper_failed"),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            transcript = self._read_transcript(out_dir, source)
        transcript = self._clean_text(transcript)
        if not transcript:
            return TranscriptResult(
                False,
                provider=self.name,
                detail="empty_transcript",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        if is_hallucinated_transcript(transcript):
            return TranscriptResult(
                False,
                text=transcript,
                provider=self.name,
                detail="hallucinated_transcript",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        return TranscriptResult(
            True, text=transcript, provider=self.name, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    def _read_transcript(self, out_dir: Path, source: Path) -> str:
        expected = out_dir / f"{source.stem}.txt"
        if expected.exists():
            return expected.read_text(encoding="utf-8", errors="replace")
        files = sorted(out_dir.glob("*.txt"))
        if files:
            return files[0].read_text(encoding="utf-8", errors="replace")
        return ""

    def _clean_text(self, text: str) -> str:
        return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()


class FasterWhisperServerSTTProvider(STTProvider):
    name = "faster_whisper_server"

    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None) -> None:
        self.base_url = (base_url or environment_value("FUSION_READER_STT_URL") or "http://127.0.0.1:8021").rstrip("/")
        self.timeout_seconds = timeout_seconds or float(
            environment_value("FUSION_READER_STT_SERVER_TIMEOUT", "60") or "60"
        )

    def health(self) -> dict:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=0.7) as resp:
                return _json_response(resp.read(), fallback={"ok": True, "provider": self.name, "url": self.base_url})
        except Exception as exc:
            return {"ok": False, "provider": self.name, "url": self.base_url, "detail": str(exc)}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        started = time.perf_counter()
        source = Path(path)
        if not source.exists() or source.stat().st_size <= 0:
            return TranscriptResult(False, provider=self.name, detail="empty_audio")
        query = urllib.parse.urlencode({"language": language or "es", "mime": mime or "application/octet-stream"})
        req = urllib.request.Request(
            f"{self.base_url}/transcribe?{query}",
            data=source.read_bytes(),
            headers={"Content-Type": mime or "application/octet-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = _json_response(resp.read())
        except urllib.error.HTTPError as exc:
            return TranscriptResult(
                False,
                provider=self.name,
                detail=f"http_{exc.code}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return TranscriptResult(
                False, provider=self.name, detail=str(exc), duration_ms=int((time.perf_counter() - started) * 1000)
            )
        if not bool(data.get("ok")):
            return TranscriptResult(
                False,
                text=str(data.get("text") or ""),
                provider=self.name,
                detail=str(data.get("error") or data.get("detail") or "stt_server_failed"),
                duration_ms=int(data.get("duration_ms") or ((time.perf_counter() - started) * 1000)),
                timings={
                    key: data.get(key) for key in ("convert_ms", "decode_ms", "duration_ms", "beam_size") if key in data
                },
            )
        transcript = str(data.get("text") or "").strip()
        timings = {key: data.get(key) for key in ("convert_ms", "decode_ms", "duration_ms", "beam_size") if key in data}
        duration_ms = int(data.get("duration_ms") or ((time.perf_counter() - started) * 1000))
        if not transcript:
            return TranscriptResult(
                False,
                provider=str(data.get("provider") or self.name),
                detail="empty_transcript",
                duration_ms=duration_ms,
                timings=timings,
            )
        if is_hallucinated_transcript(transcript):
            return TranscriptResult(
                False,
                text=transcript,
                provider=str(data.get("provider") or self.name),
                detail="hallucinated_transcript",
                duration_ms=duration_ms,
                timings=timings,
            )
        return TranscriptResult(
            True,
            text=transcript,
            provider=str(data.get("provider") or self.name),
            detail=str(data.get("detail") or ""),
            duration_ms=duration_ms,
            timings=timings,
            detected_language=str(data.get("detected_language") or data.get("language") or ""),
            segments=tuple(
                TranscriptSegment(
                    start=float(item.get("start") or 0.0),
                    end=float(item.get("end") or 0.0),
                    text=str(item.get("text") or "").strip(),
                )
                for item in (data.get("segments") or [])
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ),
        )


class AutoSTTProvider(STTProvider):
    name = "auto_stt"

    def __init__(self, primary: STTProvider | None = None, fallback: STTProvider | None = None) -> None:
        self.primary = primary or FasterWhisperServerSTTProvider()
        self.fallback = fallback or WhisperCliSTTProvider()

    def health(self) -> dict:
        primary_health = self.primary.health()
        if primary_health.get("ok"):
            return {**primary_health, "selected": self.primary.name, "fallback": self.fallback.health()}
        fallback_health = self.fallback.health()
        return {**fallback_health, "selected": self.fallback.name, "primary": primary_health}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        if self.primary.health().get("ok"):
            result = self.primary.transcribe_file(path, mime=mime, language=language)
            if result.ok:
                return result
            if result.detail == "hallucinated_transcript":
                return result
        return self.fallback.transcribe_file(path, mime=mime, language=language)


def default_stt_provider() -> STTProvider:
    selected = normalize_stt_provider(environment_value("FUSION_READER_STT_PROVIDER", "auto") or "auto")
    provider: STTProvider
    if selected == "cli":
        provider = WhisperCliSTTProvider()
    elif selected == "server":
        provider = FasterWhisperServerSTTProvider()
    else:
        provider = AutoSTTProvider()
    provider.requested_provider = selected
    return provider


def normalize_stt_provider(value: str | None) -> str:
    """Return the canonical STT mode; unknown values preserve legacy auto behavior."""
    selected = str(value or "auto").strip().lower()
    if selected in {"server", "faster_whisper", "faster-whisper"}:
        return "server"
    if selected == "cli":
        return "cli"
    return "auto"


def _default_whisper_command() -> str:
    resolved = shutil.which("whisper")
    if resolved:
        return resolved
    # Machine-local fallbacks stay overrideable via FUSION_READER_STT_COMMAND.
    for candidate in (
        "/home/linuxbrew/.linuxbrew/bin/whisper",
        "/usr/local/bin/whisper",
        "/usr/bin/whisper",
    ):
        if Path(candidate).exists():
            return candidate
    return "whisper"


def _json_response(raw: bytes, fallback: dict | None = None) -> dict:
    import json

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return fallback or {"ok": False, "detail": raw.decode("utf-8", errors="replace")}
