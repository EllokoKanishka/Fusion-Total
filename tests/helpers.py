from contextlib import contextmanager
import inspect
import os
import tempfile
import time
import wave
import threading
import unittest
from pathlib import Path
from fusion_reader_v2 import (
    AudioArtifact,
    AudioCache,
    ConversationCore,
    ExternalResearchResult,
    FusionReaderV2,
    NullChatProvider,
    NullExternalResearchBridge,
    NullSTTProvider,
    NullTTSProvider,
    ReaderNotesStore,
    STTProvider,
    TranscriptResult,
    VoiceMetricsStore,
)

_DEFAULT_AUDIO_EXPORT_ROOT = object()


def _register_test_cleanup(app) -> bool:
    # `test_app()` can be called from a unittest method without passing the
    # TestCase explicitly, so we walk upward just enough to register the
    # cleanup on the nearest active case.
    frame = inspect.currentframe()
    try:
        frame = frame.f_back
        while frame:
            owner = frame.f_locals.get("self")
            if isinstance(owner, unittest.TestCase):
                owner.addCleanup(close_test_app, app)
                return True
            frame = frame.f_back
    finally:
        del frame
    return False


def close_test_app(app, timeout: float = 10.0) -> None:
    if getattr(app, "_test_cleanup_done", False):
        return
    if getattr(app, "_test_cleanup_started", False):
        return
    app._test_cleanup_started = True
    try:
        shutdown = getattr(app, "shutdown_background_work", None)
        if callable(shutdown):
            shutdown(timeout=timeout)
        else:
            raise AssertionError("app does not expose shutdown_background_work()")
        tempdir = getattr(app, "_test_tempdir", None)
        if tempdir is not None:
            tempdir.cleanup()
            app._test_tempdir = None
        app._test_cleanup_done = True
    finally:
        app._test_cleanup_started = False


@contextmanager
def managed_test_app(
    tts=None, stt=None, root: Path | None = None, external_research=None, audio_export_root=_DEFAULT_AUDIO_EXPORT_ROOT
):
    app = test_app(
        tts=tts,
        stt=stt,
        root=root,
        external_research=external_research,
        audio_export_root=audio_export_root,
        register_cleanup=False,
    )
    try:
        yield app
    finally:
        close_test_app(app)


def test_app(
    tts=None,
    stt=None,
    root: Path | None = None,
    external_research=None,
    audio_export_root=_DEFAULT_AUDIO_EXPORT_ROOT,
    register_cleanup: bool = True,
) -> FusionReaderV2:
    tempdir = None
    if root is None:
        tempdir = tempfile.TemporaryDirectory(prefix="fusion_reader_v2_test_")
        root = Path(tempdir.name)
    else:
        root = Path(root)
    tts_provider = tts or NullTTSProvider()
    if hasattr(tts_provider, "set_output_root"):
        try:
            tts_provider.set_output_root(root / "tts_outputs")
        except Exception:
            pass
    effective_audio_export_root = (
        root / "Descargas" if audio_export_root is _DEFAULT_AUDIO_EXPORT_ROOT else audio_export_root
    )
    app = FusionReaderV2(
        tts=tts_provider,
        stt=stt or NullSTTProvider(),
        cache=AudioCache(root / "audio_cache"),
        metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        notes=ReaderNotesStore(root / "notes"),
        conversation=ConversationCore(NullChatProvider("Entendido.")),
        external_research=external_research
        or NullExternalResearchBridge(ExternalResearchResult(False, detail="bridge_unused")),
        session_state_path=root / "session_state.json",
        audio_export_root=effective_audio_export_root,
    )
    app._test_root = root
    app._test_tempdir = tempdir
    app._test_cleanup_started = False
    app._test_cleanup_done = False
    app._test_cleanup_registered = False
    if register_cleanup:
        app._test_cleanup_registered = _register_test_cleanup(app)
    return app


def wait_for_audio_export(
    app, job_id: str, timeout: float = 5.0, terminal_states: tuple[str, ...] = ("done", "cancelled", "error")
) -> dict:
    deadline = time.monotonic() + float(timeout)
    last_status: dict = {}
    while time.monotonic() < deadline:
        last_status = app.audio_export_status(job_id)
        state = str(last_status.get("state") or "")
        thread = getattr(app, "_audio_export_thread", None)
        alive = bool(thread and thread.is_alive() and thread is not threading.current_thread())
        if state in terminal_states and not alive:
            return last_status
        if state in terminal_states and alive:
            thread.join(timeout=0.05)
            continue
        time.sleep(0.01)
    raise AssertionError(
        f"audio export job {job_id} did not finish within {timeout}s; "
        f"last_state={last_status.get('state')!r}; last_detail={last_status.get('detail')!r}"
    )


class FailingTTSProvider(NullTTSProvider):
    name = "failing_tts"

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        return AudioArtifact(False, provider=self.name, detail="tts_down")


def _write_synthetic_wav(text: str, output_root: Path | None) -> Path:
    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_synthetic_", suffix=".wav", dir=str(output_root))
    else:
        fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_synthetic_", suffix=".wav")
    os.close(fd)
    path = Path(name)
    sample_rate = 16000
    frames = max(1, len(text)) * 160
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0\0" * frames)
    return path


class SyntheticWavTTSProvider(NullTTSProvider):
    name = "synthetic_wav_tts"

    def __init__(self, delay_seconds: float = 0.0, output_root: Path | None = None) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds
        self.output_root = Path(output_root) if output_root is not None else None

    def set_output_root(self, output_root: Path | str) -> None:
        self.output_root = Path(output_root)

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        path = _write_synthetic_wav(text, self.output_root)
        return AudioArtifact(True, path=path, provider=self.name, duration_ms=max(1, len(text)))


class BlockingSyntheticWavTTSProvider(SyntheticWavTTSProvider):
    name = "blocking_synthetic_wav_tts"

    def __init__(self, delay_seconds: float = 0.0, output_root: Path | None = None) -> None:
        super().__init__(delay_seconds=delay_seconds, output_root=output_root)
        self.started = threading.Event()
        self.release = threading.Event()

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        self.started.set()
        self.release.wait()
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        path = _write_synthetic_wav(text, self.output_root)
        return AudioArtifact(True, path=path, provider=self.name, duration_ms=max(1, len(text)))


class LengthLimitedSyntheticWavTTSProvider(SyntheticWavTTSProvider):
    def __init__(self, max_chars: int, delay_seconds: float = 0.0, output_root: Path | None = None) -> None:
        super().__init__(delay_seconds=delay_seconds, output_root=output_root)
        self.max_chars = max_chars

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        if len(text) > self.max_chars:
            self.calls.append((text, voice, language))
            return AudioArtifact(False, provider=self.name, detail="http_400")
        return super().synthesize(text, voice=voice, language=language)


class EmptyTranscriptSTTProvider(STTProvider):
    name = "empty_stt"

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(False, provider=self.name, detail="empty_transcript")


class HallucinatedTranscriptSTTProvider(STTProvider):
    name = "hallucinated_stt"

    def health(self) -> dict:
        return {"ok": True, "provider": self.name}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(
            False, text="¡Suscríbete!", provider=self.name, detail="hallucinated_transcript", duration_ms=12
        )


class BrokenSTTProvider(STTProvider):
    name = "broken_stt"

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "detail": "connection_refused"}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(False, provider=self.name, detail="connection_refused", duration_ms=33)


class FailingChatProvider:
    name = "failing_chat"

    def __init__(self, detail: str = "connection_refused") -> None:
        self.detail = detail
        self.calls: list[tuple[list[dict], str, dict]] = []

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "model": "broken-local", "detail": self.detail}

    def chat(self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None):
        self.calls.append((messages, model, {"think": think, "num_predict": num_predict}))
        from fusion_reader_v2.conversation import ChatResult

        return ChatResult(False, model=model or "broken-local", detail=self.detail, duration_ms=41)


def web_source() -> str:
    paths = (
        Path("fusion_reader_v2/web/server.py"),
        Path("fusion_reader_v2/web/routes/audio.py"),
        Path("fusion_reader_v2/web/routes/dialogue.py"),
        Path("fusion_reader_v2/web/routes/notes.py"),
        Path("fusion_reader_v2/web/routes/preparation.py"),
        Path("fusion_reader_v2/web/routes/reading.py"),
        Path("fusion_reader_v2/web/routes/tools.py"),
        Path("fusion_reader_v2/web/static/index.html"),
        Path("fusion_reader_v2/web/static/app.js"),
        Path("fusion_reader_v2/web/static/js/bootstrap.mjs"),
        Path("fusion_reader_v2/web/static/js/audio.mjs"),
        Path("fusion_reader_v2/web/static/js/dialogue.mjs"),
        Path("fusion_reader_v2/web/static/js/ui.mjs"),
        Path("fusion_reader_v2/web/static/js/preparation.mjs"),
        Path("fusion_reader_v2/web/static/styles.css"),
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


class NullResearchProvider:
    def __init__(self, results=None) -> None:
        self.results = results or []
        self.calls = []

    def search(self, query: str) -> list:
        self.calls.append(query)
        return self.results


class FailingResearchProvider:
    def search(self, query: str) -> list:
        raise RuntimeError("failed_to_research")


class FakeExternalResearchBridge:
    def __init__(self, result: ExternalResearchResult, *, available: bool = True) -> None:
        self.result = result
        self.available_value = available
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return self.available_value

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        self.calls.append((str(request or ""), dict(snapshot or {})))
        return self.result


def make_simple_pdf_bytes(lines: list[str]) -> bytes:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 18 Tf"]
    y = 760
    for line in lines:
        content_lines.append(f"1 0 0 1 72 {y} Tm ({esc(line)}) Tj")
        y -= 28
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("latin-1"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n").encode("latin-1")
    )
    return bytes(out)


READING_FILLER = (
    "La lectura continua necesita suficiente contexto para sostener una pagina mental coherente "
    "sin fragmentarse en unidades diminutas que vuelvan torpe la navegacion del lector."
)


def make_reading_paragraph(label: str, extra: str = "") -> str:
    parts = [str(label or "").strip(), READING_FILLER]
    if extra:
        parts.append(str(extra).strip())
    return " ".join(part for part in parts if part).strip()


def make_reading_document(label: str, paragraphs: int, extra: str = "") -> str:
    return "\n\n".join(make_reading_paragraph(f"{label} {index}.", extra=extra) for index in range(1, paragraphs + 1))


def make_reading_sections(*sections: tuple[str, str], paragraphs_per_section: int = 10) -> str:
    paragraphs: list[str] = []
    for label, marker in sections:
        for index in range(1, paragraphs_per_section + 1):
            extra = marker if index == 1 else ""
            paragraphs.append(make_reading_paragraph(f"{label} {index}.", extra=extra))
    return "\n\n".join(paragraphs)


def manual_document(doc_id: str, title: str, chunks: list[str]):
    from fusion_reader_v2 import Document

    return Document(doc_id=doc_id, title=title, text="\n\n".join(chunks), chunks=list(chunks))


class FakeUrlOpenResponse:
    def __init__(self, payload: str, status: int = 200) -> None:
        self.payload = payload.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False
