import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import zipfile
import wave
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import (
    AudioArtifact,
    AudioCache,
    AllTalkProvider,
    AutoExternalResearchBridge,
    AutoSTTProvider,
    ConversationCore,
    ExternalResearchResult,
    FasterWhisperServerSTTProvider,
    FusionReaderV2,
    NullChatProvider,
    NullExternalResearchBridge,
    NullSTTProvider,
    NullTTSProvider,
    OllamaChatProvider,
    OpenClawResearchBridge,
    SearxngResearchBridge,
    STTProvider,
    TranscriptResult,
    VoiceMetricsStore,
    WhisperCliSTTProvider,
    ReaderNotesStore,
    import_document_bytes,
    split_text,
)
from fusion_reader_v2.audio_export import concat_wav_files, sanitize_audio_title
from fusion_reader_v2.dialogue import is_hallucinated_transcript
from fusion_reader_v2.documents import clean_heading, repair_ocr_spacing, structured_plain_ocr_text
from fusion_reader_v2.pdf_to_docx import convert_pdf_to_docx, find_downloads_dir, safe_output_name


def test_app(tts=None, stt=None, root: Path | None = None, external_research=None) -> FusionReaderV2:
    root = root or Path(tempfile.mkdtemp())
    return FusionReaderV2(
        tts=tts or NullTTSProvider(),
        stt=stt or NullSTTProvider(),
        cache=AudioCache(root / "audio_cache"),
        metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        notes=ReaderNotesStore(root / "notes"),
        conversation=ConversationCore(NullChatProvider("Entendido.")),
        external_research=external_research or NullExternalResearchBridge(ExternalResearchResult(False, detail="bridge_unused")),
        session_state_path=root / "session_state.json",
    )


class FailingTTSProvider(NullTTSProvider):
    name = "failing_tts"

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        return AudioArtifact(False, provider=self.name, detail="tts_down")


class SyntheticWavTTSProvider(NullTTSProvider):
    name = "synthetic_wav_tts"

    def __init__(self, delay_seconds: float = 0.0) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_synthetic_", suffix=".wav")
        os.close(fd)
        path = Path(name)
        sample_rate = 16000
        frames = (max(1, len(text)) * 160)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\0\0" * frames)
        return AudioArtifact(True, path=path, provider=self.name, duration_ms=max(1, len(text)))


class LengthLimitedSyntheticWavTTSProvider(SyntheticWavTTSProvider):
    def __init__(self, max_chars: int, delay_seconds: float = 0.0) -> None:
        super().__init__(delay_seconds=delay_seconds)
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
        return TranscriptResult(False, text="¡Suscríbete!", provider=self.name, detail="hallucinated_transcript", duration_ms=12)


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
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
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
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
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


class FusionReaderV2Tests:
    def legacy_openclaw_bridge_defaults_to_fusion_research_agent(self):
        bridge = OpenClawResearchBridge(command="/bin/echo")
        self.assertEqual(bridge.agent, "fusion-research")

    def legacy_searxng_bridge_parses_results(self):
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080", timeout_seconds=2)
        payload = {
            "results": [
                {
                    "title": "Plato on Friendship and Eros - Stanford Encyclopedia of Philosophy",
                    "url": "https://plato.stanford.edu/entries/plato-friendship/",
                    "content": "Plato discusses love and friendship primarily in the Symposium and the Lysis.",
                }
            ]
        }
        with mock.patch("fusion_reader_v2.local_web_bridge.urlopen", return_value=FakeUrlOpenResponse(json.dumps(payload))):
            result = bridge.research("busca en internet eros en platon")
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "searxng")
        self.assertEqual(result.model, "searxng-local")
        self.assertTrue(result.sources)
        self.assertEqual(result.sources[0]["url"], "https://plato.stanford.edu/entries/plato-friendship/")
        self.assertIn("Fuentes:", result.answer)
        self.assertIn("https://plato.stanford.edu/entries/plato-friendship/", result.answer)
        self.assertNotIn("https://", result.spoken_answer)

    def legacy_searxng_bridge_handles_no_results(self):
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080", timeout_seconds=2)
        with mock.patch("fusion_reader_v2.local_web_bridge.urlopen", return_value=FakeUrlOpenResponse(json.dumps({"results": []}))):
            result = bridge.research("busca papers imposibles")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "searxng_no_results")
        self.assertIn("No encontré resultados útiles", result.answer)

    def legacy_searxng_bridge_handles_timeout(self):
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080", timeout_seconds=2)
        with mock.patch("fusion_reader_v2.local_web_bridge.urlopen", side_effect=socket.timeout("timeout")):
            result = bridge.research("busca tesis sobre diotima")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "searxng_timeout")
        self.assertIn("SearXNG local", result.answer)

    def legacy_auto_external_research_prefers_searxng_without_calling_openclaw(self):
        searxng = FakeExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="SearXNG respondió.",
                spoken_answer="SearXNG respondió.",
                detail="external_research_ok",
                provider="searxng",
                model="searxng-local",
            )
        )
        openclaw = FakeExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="OpenClaw respondió.",
                spoken_answer="OpenClaw respondió.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
            )
        )
        bridge = AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)
        result = bridge.research("busca en internet una fuente sobre eros")
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "searxng")
        self.assertEqual(len(searxng.calls), 1)
        self.assertEqual(len(openclaw.calls), 0)

    def legacy_auto_external_research_falls_back_to_openclaw_when_searxng_is_unavailable(self):
        searxng = FakeExternalResearchBridge(
            ExternalResearchResult(
                False,
                answer="SearXNG caído.",
                spoken_answer="SearXNG caído.",
                detail="searxng_unavailable",
                provider="searxng",
                model="searxng-local",
            )
        )
        openclaw = FakeExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="OpenClaw respondió.",
                spoken_answer="OpenClaw respondió.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
            )
        )
        bridge = AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)
        result = bridge.research("busca papers sobre Diotima")
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "openclaw_bridge")
        self.assertEqual(len(searxng.calls), 1)
        self.assertEqual(len(openclaw.calls), 1)

    def legacy_openclaw_bridge_humanizes_rate_limit_failures(self):
        bridge = OpenClawResearchBridge(command="/bin/echo", timeout_seconds=3)
        payload = {
            "status": "ok",
            "result": {
                "stopReason": "error",
                "meta": {"agentMeta": {"provider": "google", "model": "gemini-2.5-flash"}},
                "payloads": [{"text": "⚠️ API rate limit reached. Please try again later. (429 quota)"}],
            },
        }
        with mock.patch("fusion_reader_v2.openclaw_bridge.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["openclaw"], 0, stdout=json.dumps(payload), stderr="")
            result = bridge.research("Buscá tesis sobre Fedro.")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "bridge_rate_limit")
        self.assertIn("OpenClaw/Gemini", result.answer)
        self.assertIn("rate limit", result.answer.lower())
        self.assertIn("--agent", run.call_args.args[0])
        self.assertIn("fusion-research", run.call_args.args[0])

    def legacy_openclaw_bridge_retries_after_gateway_restart(self):
        bridge = OpenClawResearchBridge(command="/bin/echo", timeout_seconds=3)
        restart_payload = {
            "status": "ok",
            "result": {
                "stopReason": "error",
                "meta": {"agentMeta": {"provider": "google", "model": "gemini-2.5-flash"}},
                "payloads": [{"text": "gateway closed (1012): service restart"}],
            },
        }
        success_payload = {
            "status": "ok",
            "result": {
                "stopReason": "completed",
                "meta": {"agentMeta": {"provider": "google", "model": "gemini-2.5-flash"}},
                "payloads": [
                    {
                        "text": json.dumps(
                            {
                                "ok": True,
                                "query": "Fedro en El banquete",
                                "summary": "Encontré una tesis relevante.",
                                "findings": ["Una tesis doctoral lo presenta como apertura elogiosa del eros."],
                                "sources": [{"title": "Universidad X", "url": "https://ejemplo.test/tesis", "note": "tesis doctoral"}],
                                "suggested_followup": "",
                                "error": "",
                            }
                        )
                    }
                ],
            },
        }
        with mock.patch("fusion_reader_v2.openclaw_bridge.subprocess.run") as run, mock.patch("fusion_reader_v2.openclaw_bridge.time.sleep") as sleep:
            run.side_effect = [
                subprocess.CompletedProcess(["openclaw"], 0, stdout=json.dumps(restart_payload), stderr=""),
                subprocess.CompletedProcess(["openclaw"], 0, stdout=json.dumps(success_payload), stderr=""),
            ]
            result = bridge.research("Buscá tesis sobre Fedro.")
        self.assertTrue(result.ok)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()
        self.assertIn("Encontré una tesis relevante.", result.answer)

    def legacy_split_text_packs_short_paragraphs_into_page_sized_chunks(self):
        paragraph = (
            "La realidad parece una costumbre compartida, pero cada lectura la fuerza a declararse de nuevo "
            "ante la conciencia y deja una huella breve pero suficiente para sostener el siguiente pasaje."
        )
        text = "\n\n".join(paragraph for _ in range(20))
        chunks = split_text(text)
        self.assertLess(len(chunks), 20)
        self.assertTrue(all(len(chunk) >= 1200 for chunk in chunks[:-1]))

    def legacy_split_text_joins_heading_with_following_content(self):
        paragraph = (
            "La realidad parece una costumbre compartida, pero cada lectura la fuerza a declararse de nuevo "
            "ante la conciencia. Esa torsion pequena vuelve visible lo que antes pasaba por obvio. "
        )
        text = f"Capitulo 1\n\nIntroduccion\n\n{paragraph * 12}"
        chunks = split_text(text)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("Capitulo 1", chunks[0])
        self.assertIn("Introduccion", chunks[0])
        self.assertNotEqual(chunks[0].strip(), "Capitulo 1")
        self.assertNotIn("Capitulo 1", chunks[1:] if len(chunks) > 1 else [])

    def legacy_split_text_breaks_long_sentence_for_faster_tts(self):
        text = " ".join(f"palabra{i}" for i in range(120))
        chunks = split_text(text, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))

    def legacy_default_chunks_keep_a_page_sized_reading_range(self):
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda. Cuando un texto academico se despliega con paciencia, cada argumento "
            "solicita una lectura continua capaz de sostener relaciones lejanas sin perder el hilo. "
        )
        chunks = split_text("\n\n".join(paragraph for _ in range(12)))
        self.assertTrue(all(len(chunk) <= 3200 for chunk in chunks))
        self.assertTrue(all(len(chunk) >= 1200 for chunk in chunks[:-1]))

    def legacy_split_text_skips_pdf_zero_noise(self):
        paragraph = (
            "Una frase suficientemente larga para demostrar que el ruido cero debe desaparecer mientras el texto "
            "real se mantiene unido al resto del contenido legible del documento."
        )
        chunks = split_text(f"{paragraph}\n\n0\n\n{paragraph}\n\n{paragraph}")
        self.assertEqual(len(chunks), 1)
        self.assertNotIn("\n\n0\n\n", chunks[0])

    def legacy_split_text_avoids_one_word_chunks(self):
        paragraph = (
            "La lectura continua necesita contexto suficiente para que una linea breve no quede aislada "
            "como si fuera un bloque completo de navegacion."
        )
        text = "Uno.\n\nDos.\n\nTres.\n\n" + "\n\n".join(paragraph for _ in range(10))
        chunks = split_text(text)
        stripped = [chunk.strip() for chunk in chunks]
        self.assertNotIn("Uno.", stripped)
        self.assertNotIn("Dos.", stripped)
        self.assertNotIn("Tres.", stripped)

    def legacy_split_text_splits_very_long_paragraph_by_sentence(self):
        sentence = (
            "Esta oracion extensa recorre matices, agrega ejemplos, enlaza autores y vuelve sobre una hipotesis "
            "anterior para observar como la lectura continua necesita sostener una corriente semantica sin un corte "
            "brusco que fracture la percepcion del argumento. "
        )
        text = (sentence * 40).strip()
        chunks = split_text(text)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 3200 for chunk in chunks))
        self.assertTrue(all(" " in chunk for chunk in chunks))

    def legacy_document_from_text_uses_page_sized_chunks_by_default(self):
        from fusion_reader_v2.reader import Document

        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        document = Document.from_text("doc", "Doc", "\n\n".join(paragraph for _ in range(30)))
        self.assertGreater(len(document.chunks), 1)
        self.assertTrue(all(len(chunk) <= 3200 for chunk in document.chunks))
        self.assertTrue(all(len(chunk) >= 1200 for chunk in document.chunks[:-1]))

    def legacy_split_text_respects_explicit_max_chars_for_compatibility(self):
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        chunks = split_text("\n\n".join(paragraph for _ in range(8)), max_chars=420)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 420 for chunk in chunks))

    def legacy_reader_load_and_navigation(self):
        app = test_app()
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        app.load_text("doc", "Doc", "\n\n".join(f"{paragraph}{i}." for i in range(40)))
        self.assertEqual(app.status()["current"], 1)
        first = app.status()["text"]
        second = app.next()["text"]
        self.assertNotEqual(first, second)
        self.assertEqual(app.previous()["text"], first)
        jumped = app.jump(3)["text"]
        self.assertEqual(app.status()["current"], 3)
        self.assertEqual(app.status()["text"], jumped)

    def legacy_reader_session_navigation_still_reports_current_total(self):
        app = test_app()
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        app.load_text("doc", "Doc", "\n\n".join(paragraph for _ in range(40)))
        initial = app.status()
        self.assertGreater(initial["total"], 2)
        self.assertEqual(initial["current"], 1)
        app.next()
        self.assertEqual(app.status()["current"], 2)
        app.jump(app.status()["total"])
        self.assertEqual(app.status()["current"], app.status()["total"])
        app.previous()
        self.assertEqual(app.status()["current"], app.status()["total"] - 1)

    def legacy_short_document_remains_single_chunk(self):
        chunks = split_text("Documento corto con una sola idea y un cierre breve.")
        self.assertEqual(len(chunks), 1)

    def legacy_voice_cache_reuses_audio(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        first = app.test_voice("Hola mundo")
        second = app.test_voice("Hola mundo")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(provider.calls), 1)

    def legacy_default_voice_is_lucy_female_03_and_can_be_overridden(self):
        previous = os.environ.get("FUSION_READER_VOICE")
        try:
            os.environ.pop("FUSION_READER_VOICE", None)
            self.assertEqual(test_app().voice.voice, "female_03.wav")
            self.assertEqual(AllTalkProvider(base_url="http://example.invalid").default_voice, "female_03.wav")
            os.environ["FUSION_READER_VOICE"] = "female_06.wav"
            self.assertEqual(test_app().voice.voice, "female_06.wav")
            self.assertEqual(AllTalkProvider(base_url="http://example.invalid").default_voice, "female_06.wav")
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_VOICE", None)
            else:
                os.environ["FUSION_READER_VOICE"] = previous

    def legacy_alltalk_audio_url_uses_configured_local_port(self):
        provider = AllTalkProvider(base_url="http://127.0.0.1:7899")
        self.assertEqual(
            provider._audio_url("http://127.0.0.1:7851/outputs/fusion.wav"),
            "http://127.0.0.1:7899/outputs/fusion.wav",
        )

    def legacy_alltalk_default_uses_fusion_gpu_port_and_requires_owner(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
            "FUSION_READER_TTS_OWNER_FILE",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.environ.pop("FUSION_READER_ALLTALK_URL", None)
                os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
                os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
                os.environ["FUSION_READER_TTS_OWNER_FILE"] = str(Path(tmp) / "missing_owner.json")
                provider = AllTalkProvider()
                self.assertEqual(provider.base_url, "http://127.0.0.1:7853")
                health = provider.health()
                self.assertFalse(health["ok"])
                self.assertIn("tts_owner_missing", health["detail"])
                artifact = provider.synthesize("Hola")
                self.assertFalse(artifact.ok)
                self.assertIn("tts_owner_missing", artifact.detail)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def legacy_alltalk_prefers_owned_gpu_url_over_cpu_fallback_env(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_CPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
            "FUSION_READER_TTS_OWNER_FILE",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            owner_file = Path(tmp) / "tts_owner.json"
            owner_file.write_text(
                json.dumps(
                    {
                        "owner": "fusion_reader_v2",
                        "port": 7853,
                        "owner_pid": os.getpid(),
                    }
                ),
                encoding="utf-8",
            )
            try:
                os.environ["FUSION_READER_ALLTALK_URL"] = "http://127.0.0.1:7851"
                os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
                os.environ["FUSION_READER_CPU_TTS_PORT"] = "7851"
                os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
                os.environ["FUSION_READER_TTS_OWNER_FILE"] = str(owner_file)
                with mock.patch.object(AllTalkProvider, "_gpu_service_ready", return_value=True), mock.patch.object(
                    AllTalkProvider, "_owner_guard", return_value=(True, "")
                ):
                    provider = AllTalkProvider()
                self.assertEqual(provider.base_url, "http://127.0.0.1:7853")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def legacy_alltalk_keeps_cpu_fallback_when_gpu_not_ready(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_CPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["FUSION_READER_ALLTALK_URL"] = "http://127.0.0.1:7851"
            os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
            os.environ["FUSION_READER_CPU_TTS_PORT"] = "7851"
            os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
            with mock.patch.object(AllTalkProvider, "_gpu_service_ready", return_value=False), mock.patch.object(
                AllTalkProvider, "_owner_guard", return_value=(False, "tts_owner_missing")
            ):
                provider = AllTalkProvider()
            self.assertEqual(provider.base_url, "http://127.0.0.1:7851")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def legacy_alltalk_rejects_doctora_and_historic_ports_even_when_configured(self):
        keys = ["FUSION_READER_REQUIRE_TTS_OWNER", "LUCY_TTS_PORT"]
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "0"
            os.environ["LUCY_TTS_PORT"] = "7854"
            cases = [
                ("http://127.0.0.1:7854", "tts_foreign_doctora_lucy_port"),
                ("http://127.0.0.1:7852", "tts_historic_unassigned_port"),
            ]
            for url, detail in cases:
                provider = AllTalkProvider(base_url=url)
                health = provider.health()
                self.assertFalse(health["ok"])
                self.assertIn(detail, health["detail"])
                self.assertEqual(provider.voices(), [])
                artifact = provider.synthesize("Hola")
                self.assertFalse(artifact.ok)
                self.assertIn(detail, artifact.detail)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def legacy_fusion_launchers_do_not_auto_claim_antigravity_tts_port(self):
        root = Path(__file__).resolve().parents[1]
        launchers = [
            root / "scripts" / "start_fusion_reader_v2.sh",
            root / "scripts" / "open_fusion_reader.sh",
            root / "scripts" / "start_reader_neural_tts_gpu_5090.sh",
        ]
        for launcher in launchers:
            text = launcher.read_text(encoding="utf-8")
            self.assertNotIn("DIRECT_CHAT_ALLTALK_GPU_PORT", text)
            self.assertNotIn("127.0.0.1:7852", text)
        self.assertIn("FUSION_READER_GPU_TTS_PORT:-7853", launchers[0].read_text(encoding="utf-8"))

    def legacy_fusion_launchers_require_owned_gpu_tts(self):
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "scripts/start_reader_neural_tts_gpu_5090.sh",
            "scripts/start_fusion_reader_v2.sh",
            "scripts/open_fusion_reader.sh",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertIn("FUSION_READER_TTS_OWNER_FILE", text)
            self.assertIn('"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"', text)
            self.assertIn("owner_pid", text)
            self.assertNotIn('if [[ -z "$owner_pid" ]]; then\n    return 0', text)

    def legacy_fusion_launcher_waits_for_owned_gpu_tts_before_cpu_fallback(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn('FUSION_READER_GPU_TTS_WAIT_SECONDS', text)
        self.assertIn('fusion_gpu_ready()', text)
        self.assertIn('while (( $(date +%s) < gpu_wait_deadline )); do', text)
        self.assertIn('sleep 1', text)
        self.assertIn('owner valido', text)
        self.assertIn('Fusion TTS URL selected:', text)
        self.assertIn('Fusion TTS fallback selected:', text)
        self.assertNotIn("127.0.0.1:7852", text)

    def legacy_fusion_launcher_has_persistent_log_and_pid_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn('RUNTIME_DIR="${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}"', text)
        self.assertIn('LOG_DIR="${FUSION_READER_LOG_DIR:-$RUNTIME_DIR/logs}"', text)
        self.assertIn('fusion_reader_v2_server.log', text)
        self.assertIn('fusion_reader_v2.pid', text)
        self.assertIn('API/UI port: ${PORT}', text)
        self.assertIn('if [[ -n "$existing_pid" ]]; then', text)
        self.assertIn('curl -fsS --max-time 2 "$startup_status_url"', text)
        self.assertIn('Fusion Reader v2 health OK', text)

    def legacy_server_ui_surfaces_tts_gpu_and_cpu_fallback_modes(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("describeTtsStatus", text)
        self.assertIn("TTS GPU 7853 listo", text)
        self.assertIn("TTS CPU 7851 fallback", text)
        self.assertIn("TTS no disponible", text)
        self.assertIn("TTS listo", text)
        self.assertIn("services.tts", text)
        self.assertNotIn(":7854", text)
        self.assertNotIn(":7852", text)

    def legacy_server_ui_surfaces_active_stt_provider_and_fallback_state(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('id="sttChip"', text)
        self.assertIn('id="sttStatus"', text)
        self.assertIn("describeSttStatus", text)
        self.assertIn("whisper_cli", text)
        self.assertIn("fallback operativo", text)
        self.assertIn("primario 8021 offline", text)
        self.assertIn("faster-whisper 8021", text)
        self.assertIn("faster_whisper_server", text)
        self.assertIn("services.stt", text)

    def legacy_server_ui_contains_pdf_to_word_tool_without_using_normal_load_flow(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("PDF → Word", text)
        self.assertIn("/api/tools/pdf-to-docx", text)
        self.assertIn('id="pdfToWordInput"', text)
        self.assertIn('accept=".pdf,application/pdf"', text)
        self.assertIn("convertPdfToWord(", text)
        self.assertIn("Descargar", text)

    def legacy_server_read_current_does_not_render_audio_result_as_status(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        read_start = text.index("async function readCurrent()")
        read_end = text.index("async function pollPrepare()", read_start)
        read_current = text[read_start:read_end]
        self.assertIn("const data = await api('/api/read'", read_current)
        self.assertNotIn("renderStatus(data)", read_current)
        self.assertIn("playAudio(data, sequence, request)", read_current)

    def legacy_server_ui_resets_reader_viewport_only_on_real_block_changes(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("function resetReaderViewport()", server)
        self.assertIn("document.querySelector('.reader')", server)
        self.assertIn("reader.scrollTop = 0;", server)
        self.assertIn("const didChangeViewport = nextDocId !== lastRenderedDocId || nextBlockIndex !== lastRenderedBlockIndex || header.chunk !== lastRenderedBlockText;", server)
        self.assertIn("if (didChangeViewport) {", server)
        self.assertIn("resetReaderViewport();", server)

    def legacy_server_ui_reader_layout_starts_chunks_from_top(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn(".reader {\n      overflow: auto;\n      padding: 24px clamp(26px, 3vw, 42px) 14px;\n      display: flex;\n      justify-content: center;\n      align-items: stretch;", server)
        chunk_start = server.index(".chunk {")
        chunk_end = server.index(".chunk.empty {", chunk_start)
        chunk_css = server[chunk_start:chunk_end]
        self.assertIn("width: 100%;", chunk_css)
        self.assertIn("max-width: 72ch;", chunk_css)
        self.assertIn("margin: 0;", chunk_css)
        self.assertNotIn("max-width: 980px;", chunk_css)
        self.assertNotIn("transform: translateY(-8%);", chunk_css)

    def legacy_server_ui_document_header_prefers_loaded_document_state(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        header_start = server.index("function documentHeaderState(data) {")
        header_end = server.index("function dialogueAppliedReasoningLabel(data) {", header_start)
        header_logic = server[header_start:header_end]
        self.assertIn("const loaded = Boolean(item.loaded);", header_logic)
        self.assertIn("const documentActive = Boolean(loaded || usesDocument || (title && (current > 0 || total > 0 || hasChunkText)));", header_logic)
        self.assertIn("const documentAvailable = Boolean(anchor.document_available !== undefined ? anchor.document_available : documentActive);", header_logic)
        self.assertIn("if (documentActive && title) {", header_logic)
        self.assertIn("Documento — ${title}", header_logic)
        self.assertIn("Modo libre", header_logic)
        self.assertIn("Sin documento activo", header_logic)

    def legacy_server_ui_contains_audio_export_controls_and_endpoint(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("Exportar audio", server)
        self.assertIn("Bloque actual", server)
        self.assertIn("Bloque específico", server)
        self.assertIn("Rango", server)
        self.assertIn("Documento completo", server)
        self.assertIn("/api/audio-export", server)
        self.assertIn("Descargar WAV", server)
        self.assertIn("function renderAudioExportStatus", server)
        self.assertNotIn("translateY(-8%);", server)

    def legacy_dialogue_microphone_capture_diagnostics_are_exposed(self):
        root = Path(__file__).resolve().parents[1]
        server = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        service = (root / "fusion_reader_v2" / "service.py").read_text(encoding="utf-8")
        for token in (
            "dialoguePcmStats",
            "mic_rms",
            "mic_peak",
            "voice_detected",
            "cut_reason",
            "audio_size_bytes",
            "Mic:",
        ):
            self.assertIn(token, server)
        self.assertIn("audio_meta", service)
        self.assertIn("audio_size_bytes", service)
        self.assertIn("audio_mime", service)
        self.assertNotIn("API_KEY", server)
        self.assertNotIn("TOKEN", server)

    def legacy_dialogue_ui_reports_microphone_permission_states(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("async function microphonePermissionState()", server)
        self.assertIn("Permiso de micrófono pendiente. Aprobalo en el navegador para empezar a escuchar.", server)
        self.assertIn("El micrófono está bloqueado en el navegador. Permitilo para usar Dialogar.", server)
        self.assertIn("El micrófono está bloqueado o fue rechazado. Permitilo en el navegador y volvé a intentar.", server)

    def legacy_dialogue_audio_trace_keeps_microphone_diagnostics(self):
        app = test_app(stt=EmptyTranscriptSTTProvider())
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            handle.write(b"RIFF" + b"\0" * 2400)
            handle.flush()
            out = app.dialogue_turn_audio(
                handle.name,
                mime="audio/wav",
                audio_meta={
                    "audio_size_bytes": "2444",
                    "capture_ms": "850",
                    "mic_rms": "0.031",
                    "mic_peak": "0.14",
                    "voice_detected": "1",
                    "cut_reason": "silence",
                },
            )
        trace = out["trace"]
        self.assertEqual(trace["audio_size_bytes"], 2444)
        self.assertEqual(trace["audio_mime"], "audio/wav")
        self.assertEqual(trace["capture_ms"], 850)
        self.assertEqual(trace["mic_rms"], 0.031)
        self.assertEqual(trace["mic_peak"], 0.14)
        self.assertTrue(trace["voice_detected"])
        self.assertEqual(trace["cut_reason"], "silence")

    def legacy_voice_port_isolation_verifier_covers_doctora_memory_sources(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify_voice_port_isolation.sh").read_text(encoding="utf-8")
        self.assertIn("n8n_data/boveda_lucy.sqlite", text)
        self.assertIn("data/lucy_bunker_log.jsonl", text)
        self.assertIn("Taverna-legacy/alltalk_tts", text)
        self.assertIn("VOICE_RELEVANT_PATTERN", text)
        self.assertIn("latest_relevant_doctora_boveda", text)
        self.assertIn("latest_relevant_doctora_bunker", text)
        self.assertIn("no relevant Doctora voice/TTS entry found", text)
        self.assertIn('"puerto_fusion":7852', text)
        self.assertIn('"alltalk_port":7851', text)
        self.assertIn("--port $HISTORIC_PORT", text)
        self.assertIn("latest relevant Doctora boveda entry", text)
        self.assertIn("latest relevant Doctora bunker entry", text)

    def legacy_read_current_prefetches_next(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", make_reading_document("Doc", 24))
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertIn("ready_ms", out)
        self.assertIn("synthesis_ms", out)
        status = app.status()
        self.assertEqual(status["prefetch_index"], 1)

    def legacy_read_current_reuses_current_prefetch(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.")
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertEqual(len(provider.calls), 1)

    def legacy_prepare_document_caches_all_chunks_in_background(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", make_reading_document("Doc", 36), prefetch=False)
        started = app.prepare_document()
        self.assertEqual(started["status"], "running")
        for _ in range(50):
            status = app.prepare_status()
            if status["status"] == "done":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "done")
        self.assertGreater(app.prepare_status()["generated"], 1)
        self.assertEqual(app.prepare_status()["generated"], app.prepare_status()["total"])
        before = len(provider.calls)
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertTrue(out["cached"])
        self.assertEqual(len(provider.calls), before)

    def legacy_load_text_resets_previous_prepare_status(self):
        app = test_app()
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        app.prepare_document()
        for _ in range(50):
            if app.prepare_status()["status"] == "done":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "done")
        app.load_text("new", "Nuevo", "Dos.", prefetch=False)
        self.assertEqual(app.prepare_status()["status"], "idle")
        self.assertEqual(app.prepare_status()["doc_id"], "")

    def legacy_audio_export_current_block_uses_current_cursor(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos", "tres"]))
        app.jump(2)
        started = app.start_audio_export("current")
        self.assertEqual(started["state"], "queued")
        for _ in range(100):
            status = app.audio_export_status(started["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        status = app.audio_export_status(started["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["start_block"], 2)
        self.assertEqual(status["end_block"], 2)
        self.assertEqual(provider.calls[0][0], "dos")

    def legacy_audio_export_specific_block(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos", "tres"]))
        started = app.start_audio_export("block", block=3)
        for _ in range(100):
            status = app.audio_export_status(started["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        status = app.audio_export_status(started["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["start_block"], 3)
        self.assertEqual(status["end_block"], 3)
        self.assertEqual(provider.calls[0][0], "tres")

    def legacy_audio_export_range(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos", "tres", "cuatro"]))
        started = app.start_audio_export("range", start=2, end=4)
        for _ in range(100):
            status = app.audio_export_status(started["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        status = app.audio_export_status(started["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["start_block"], 2)
        self.assertEqual(status["end_block"], 4)
        self.assertEqual([call[0] for call in provider.calls], ["dos", "tres", "cuatro"])

    def legacy_audio_export_full_document(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos", "tres"]))
        started = app.start_audio_export("full")
        for _ in range(100):
            status = app.audio_export_status(started["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        status = app.audio_export_status(started["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["start_block"], 1)
        self.assertEqual(status["end_block"], 3)
        self.assertEqual([call[0] for call in provider.calls], ["uno", "dos", "tres"])

    def legacy_audio_export_rejects_invalid_ranges_and_missing_document(self):
        app = test_app(tts=SyntheticWavTTSProvider())
        self.assertEqual(app.start_audio_export("current")["error"], "no_document_loaded")
        app.session.load(manual_document("doc", "Doc", ["uno", "dos"]))
        self.assertEqual(app.start_audio_export("block", block=0)["error"], "audio_export_block_out_of_range")
        self.assertEqual(app.start_audio_export("range", start=2, end=1)["error"], "audio_export_range_invalid")

    def legacy_audio_export_uses_snapshot_even_if_session_changes(self):
        provider = SyntheticWavTTSProvider(delay_seconds=0.03)
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Original", ["uno", "dos", "tres"]))
        started = app.start_audio_export("full")
        app.session.load(manual_document("other", "Nuevo", ["cambio"]))
        for _ in range(200):
            status = app.audio_export_status(started["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        status = app.audio_export_status(started["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["title"], "Original")
        self.assertEqual([call[0] for call in provider.calls], ["uno", "dos", "tres"])

    def legacy_audio_export_reuses_cache_without_calling_tts_again(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos"]))
        first = app.start_audio_export("range", start=1, end=2)
        for _ in range(100):
            status = app.audio_export_status(first["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        before = len(provider.calls)
        second = app.start_audio_export("range", start=1, end=2)
        for _ in range(100):
            status = app.audio_export_status(second["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        status = app.audio_export_status(second["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["cached_blocks"], 2)
        self.assertEqual(len(provider.calls), before)

    def legacy_audio_export_cancel_sets_cancelled_state(self):
        provider = SyntheticWavTTSProvider(delay_seconds=0.05)
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos", "tres"]))
        started = app.start_audio_export("full")
        time.sleep(0.02)
        app.cancel_audio_export(started["job_id"])
        for _ in range(200):
            status = app.audio_export_status(started["job_id"])
            if status["state"] in {"cancelled", "done", "error"}:
                break
            time.sleep(0.01)
        status = app.audio_export_status(started["job_id"])
        self.assertEqual(status["state"], "cancelled")

    def legacy_audio_export_download_stays_in_descargas(self):
        provider = SyntheticWavTTSProvider()
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "Descargas"
            downloads.mkdir(parents=True, exist_ok=True)
            app = test_app(tts=provider, root=Path(tmp) / "state")
            app.session.load(manual_document("doc", "../Doc peligroso", ["uno"]))
            with mock.patch("fusion_reader_v2.audio_export.find_downloads_dir", return_value=downloads), \
                 mock.patch("fusion_reader_v2.service.find_downloads_dir", return_value=downloads):
                started = app.start_audio_export("full")
                for _ in range(100):
                    status = app.audio_export_status(started["job_id"])
                    if status["state"] == "done":
                        break
                    time.sleep(0.01)
                status = app.audio_export_status(started["job_id"])
            self.assertEqual(status["state"], "done")
            target = Path(status["output_path"]).resolve()
            self.assertTrue(target.is_file())
            self.assertEqual(target.parent, downloads.resolve())
            self.assertNotIn("..", target.name)

    def legacy_audio_export_does_not_break_read_current(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["uno", "dos"]))
        started = app.start_audio_export("current")
        for _ in range(100):
            status = app.audio_export_status(started["job_id"])
            if status["state"] == "done":
                break
            time.sleep(0.01)
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertTrue(out["cached"])

    def legacy_audio_export_and_read_current_split_long_tts_requests_when_provider_rejects_big_input(self):
        provider = LengthLimitedSyntheticWavTTSProvider(max_chars=120)
        app = test_app(tts=provider)
        app.tts_segment_chars = 120
        long_text = " ".join(["Frase bastante larga para sintetizar."] * 12)
        app.session.load(manual_document("doc", "Doc", [long_text]))
        export_job = app.start_audio_export("current")
        for _ in range(100):
            status = app.audio_export_status(export_job["job_id"])
            if status["state"] in {"done", "error"}:
                break
            time.sleep(0.01)
        status = app.audio_export_status(export_job["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertGreater(len(provider.calls), 2)
        self.assertTrue(any(len(call[0]) <= 120 for call in provider.calls[1:]))
        app.cache.root.joinpath(app.cache.key(long_text, app.voice.voice, app.voice.language) + ".wav").unlink(missing_ok=True)
        provider.calls.clear()
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertGreater(len(provider.calls), 2)

    def legacy_concat_wav_files_creates_valid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = []
            for index in range(3):
                path = root / f"in_{index}.wav"
                with wave.open(str(path), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(16000)
                    wav_file.writeframes(b"\0\0" * 1600)
                inputs.append(path)
            output = root / "out.wav"
            method = concat_wav_files(inputs, output)
            self.assertEqual(method, "wave")
            self.assertTrue(output.exists())
            with wave.open(str(output), "rb") as wav_file:
                self.assertEqual(wav_file.getnchannels(), 1)
                self.assertEqual(wav_file.getframerate(), 16000)
                self.assertGreater(wav_file.getnframes(), 1600)

    def legacy_audio_export_filename_sanitizer_blocks_path_traversal(self):
        self.assertEqual(sanitize_audio_title("../Doc peligroso"), "Doc_peligroso")

    def legacy_reference_documents_can_be_added_without_replacing_main(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.\n\nSegundo bloque.", prefetch=False)
        out = app.add_reference_text("ref", "Consulta", "Texto de consulta.\n\nOtro dato.")
        self.assertTrue(out["ok"])
        status = app.status()
        self.assertEqual(status["doc_id"], "doc")
        self.assertEqual(status["main_document"]["title"], "Principal")
        self.assertEqual(len(status["reference_documents"]), 1)
        self.assertEqual(status["reference_documents"][0]["doc_id"], "ref")

    def legacy_status_free_mode_with_document_loaded_exposes_anchor_without_document_use(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.set_laboratory_mode("free")
        status = app.status()
        self.assertTrue(status["document"]["loaded"])
        self.assertEqual(status["anchor"]["mode"], "free")
        self.assertFalse(status["anchor"]["uses_document"])
        self.assertTrue(status["anchor"]["document_available"])

    def legacy_status_document_mode_with_document_loaded_exposes_anchor_use(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.set_laboratory_mode("document")
        status = app.status()
        self.assertTrue(status["document"]["loaded"])
        self.assertEqual(status["anchor"]["mode"], "document")
        self.assertTrue(status["anchor"]["uses_document"])
        self.assertTrue(status["anchor"]["document_available"])

    def legacy_status_after_clear_document_exposes_no_document_available(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.set_laboratory_mode("free")
        app.clear_document()
        status = app.status()
        self.assertFalse(status["document"]["loaded"])
        self.assertFalse(status["anchor"]["document_available"])
        self.assertFalse(status["anchor"]["uses_document"])

    def legacy_promote_reference_swaps_main_and_previous_main_becomes_reference(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        out = app.promote_reference_document("ref", prefetch=False)
        self.assertTrue(out["ok"])
        status = app.status()
        self.assertEqual(status["doc_id"], "ref")
        self.assertEqual(status["title"], "Consulta")
        refs = status["reference_documents"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["doc_id"], "doc")

    def legacy_restart_restores_reference_documents(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        reopened = test_app(root=root)
        status = reopened.status()
        self.assertEqual(status["doc_id"], "doc")
        self.assertEqual(len(status["reference_documents"]), 1)
        self.assertEqual(status["reference_documents"][0]["title"], "Consulta")

    def legacy_reasoning_mode_defaults_to_thinking_when_env_is_not_forcing_normal(self):
        previous_mode = os.environ.get("FUSION_READER_REASONING_MODE")
        previous_think = os.environ.get("FUSION_READER_CHAT_THINK")
        try:
            os.environ.pop("FUSION_READER_REASONING_MODE", None)
            os.environ.pop("FUSION_READER_CHAT_THINK", None)
            app = test_app()
            self.assertEqual(app.reasoning_status()["mode"], "thinking")
        finally:
            if previous_mode is None:
                os.environ.pop("FUSION_READER_REASONING_MODE", None)
            else:
                os.environ["FUSION_READER_REASONING_MODE"] = previous_mode
            if previous_think is None:
                os.environ.pop("FUSION_READER_CHAT_THINK", None)
            else:
                os.environ["FUSION_READER_CHAT_THINK"] = previous_think

    def legacy_reasoning_mode_switch_persists_across_restart(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        changed = app.set_reasoning_mode("supreme")
        self.assertEqual(changed["mode"], "supreme")
        reopened = test_app(root=root)
        self.assertEqual(reopened.reasoning_status()["mode"], "supreme")

    def legacy_chat_uses_selected_reasoning_mode_settings(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.chat("¿Qué ves?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode"], "normal")
        self.assertEqual(out["reasoning_passes"], 1)
        self.assertEqual(len(chat_provider.calls), 1)
        self.assertFalse(chat_provider.calls[0][2]["think"])

    def legacy_normal_mode_chat_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("compañera humana de lectura", prompt)
        self.assertIn("iluminar y tensionar", prompt)
        self.assertIn("No digas que te llamás Fusion", prompt)
        self.assertIn("identidad tiene prioridad", prompt)

    def legacy_normal_mode_dialogue_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.dialogue_turn_text("¿Qué opinás del bloque?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("pensamiento compartido", prompt)
        self.assertIn("melancolía sobria", prompt)
        self.assertIn("No digas que te llamás Fusion", prompt)

    def legacy_thinking_mode_chat_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("Leé con más calma", prompt)
        self.assertIn("agregá capas", prompt)
        self.assertIn("No digas que te llamás Fusion", prompt)

    def legacy_thinking_mode_dialogue_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.dialogue_turn_text("¿Qué opinás del bloque?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("Leé con más calma", prompt)
        self.assertIn("melancolía sobria", prompt)

    def legacy_free_laboratory_mode_chat_prompt_is_not_forced_back_to_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos de física cuántica.")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Estás en modo libre", prompt)
        self.assertIn("Los documentos son contexto opcional", prompt)
        self.assertEqual(app.laboratory_mode_status()["mode"], "free")

    def legacy_supreme_mode_keeps_free_laboratory_mode_in_final_pass(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Estás en modo libre", final_prompt)
        self.assertIn("Los documentos son contexto opcional", final_prompt)

    def legacy_contrapunto_mode_keeps_free_laboratory_mode_in_synthesis_pass(self):
        chat_provider = NullChatProvider("Síntesis final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("pensamiento_critico")
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        synthesis_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Estás en modo libre", synthesis_prompt)
        self.assertIn("Los documentos son contexto opcional", synthesis_prompt)

    def legacy_supreme_mode_keeps_document_mode_in_final_pass(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.set_laboratory_mode("document")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertNotIn("Estás en modo libre", final_prompt)
        self.assertNotIn("Los documentos son contexto opcional", final_prompt)

    def legacy_contrapunto_mode_keeps_document_mode_in_synthesis_pass(self):
        chat_provider = NullChatProvider("Síntesis final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("pensamiento_critico")
        app.set_laboratory_mode("document")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        synthesis_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertNotIn("Estás en modo libre", synthesis_prompt)
        self.assertNotIn("Los documentos son contexto opcional", synthesis_prompt)

    def legacy_supreme_mode_chat_prompt_reuses_thinking_lucy_persona(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        draft_prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Lucy Cunningham", draft_prompt)
        self.assertIn("depurá tus conceptos", draft_prompt)
        self.assertIn("No digas que te llamás Fusion", draft_prompt)
        self.assertIn("Lucy Cunningham", final_prompt)
        self.assertIn("depurá tus conceptos", final_prompt)

    def legacy_supreme_reasoning_runs_three_passes(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.chat("Pensá este fragmento con profundidad.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode"], "supreme")
        self.assertEqual(out["reasoning_passes"], 3)
        self.assertEqual(len(chat_provider.calls), 3)
        self.assertTrue(all(call[2]["think"] for call in chat_provider.calls))
        self.assertIn("REVISION INTERNA", chat_provider.calls[-1][0][1]["content"])

    def legacy_dialogue_degrades_supreme_to_thinking_by_default(self):
        chat_provider = NullChatProvider("Entendido.")
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode_requested"], "supreme")
        self.assertEqual(out["reasoning_mode_applied"], "thinking")
        self.assertTrue(out["reasoning_degraded"])
        self.assertEqual(out["reasoning_mode"], "thinking")
        self.assertEqual(out["reasoning_passes"], 1)
        self.assertEqual(len(chat_provider.calls), 1)
        self.assertTrue(chat_provider.calls[0][2]["think"])
        trace_path = root / "dialogue_trace.jsonl"
        self.assertTrue(trace_path.exists())
        logged = trace_path.read_text(encoding="utf-8")
        self.assertIn('"event": "dialogue_turn_text"', logged)
        self.assertIn('"requested_mode": "supreme"', logged)
        self.assertIn('"applied_mode": "thinking"', logged)

    def legacy_reasoning_catalog_includes_pensamiento_critico(self):
        app = test_app()
        catalog = app.conversation.reasoning_catalog()
        modes = [item["mode"] for item in catalog]
        self.assertIn("pensamiento_critico", modes)
        critico = next(item for item in catalog if item["mode"] == "pensamiento_critico")
        self.assertIn("Pensamiento crítico", critico["label"])
        self.assertEqual(critico["passes"], 3)
        self.assertTrue(critico["think"])

    def legacy_contrapunto_textual_runs_three_passes(self):
        chat_provider = NullChatProvider("Respuesta final dialéctica.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("contrapunto")
        app.load_text("doc", "Doc", "Contexto base.", prefetch=False)
        out = app.chat("Analizá este fragmento.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode"], "pensamiento_critico")
        self.assertEqual(out["reasoning_passes"], 3)
        self.assertEqual(len(chat_provider.calls), 3)
        # Verificar que al menos una llamada es del Auditor/Crítico
        found_auditor = False
        for call in chat_provider.calls:
            msgs = call[0]
            for m in msgs:
                if m["role"] == "system" and ("Auditor" in m["content"] or "Critico" in m["content"] or "Antitesis" in m["content"]):
                    found_auditor = True
                    break
        self.assertTrue(found_auditor, "No se encontró el rol de Auditor/Crítico en las llamadas al provider")
        self.assertEqual(out["detail"], "pensamiento_critico_dialectical_3pass")

    def legacy_contrapunto_does_not_break_supreme(self):
        chat_provider = NullChatProvider("Respuesta final supreme.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Contexto.", prefetch=False)
        out = app.chat("Pensá profundo.")
        self.assertEqual(out["reasoning_mode"], "supreme")
        self.assertEqual(out["reasoning_passes"], 3)
        self.assertEqual(out["detail"], "supreme_3pass")

    def legacy_dialogue_degrades_contrapunto_to_thinking(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("contrapunto")
        app.load_text("doc", "Doc", "Contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué ves?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode_requested"], "pensamiento_critico")
        self.assertEqual(out["reasoning_mode_applied"], "thinking")
        self.assertTrue(out["reasoning_degraded"])
        # Verificar vía dialogue_status que la razón es correcta
        status = app.dialogue_status()
        self.assertEqual(status["dialogue_reasoning"]["degraded_reason"], "dialogue_pensamiento_critico_degraded_to_thinking")

    def legacy_server_ui_contains_pensamiento_critico_button(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('id="reasoningPensamientoCriticoBtn"', text)
        self.assertIn("Pensamiento crítico", text)
        self.assertIn("setReasoningMode('pensamiento_critico')", text)
        # Verificar que no se eliminaron los anteriores
        self.assertIn('id="reasoningNormalBtn"', text)
        self.assertIn('id="reasoningThinkingBtn"', text)
        self.assertIn('id="reasoningSupremeBtn"', text)

    def legacy_contrapunto_synthesis_prompt_has_style_restrictions(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "fusion_reader_v2" / "conversation.py").read_text(encoding="utf-8")
        # Verificar que existen las restricciones de estilo en el código fuente
        self.assertIn("EMPEZA DIRECTAMENTE", source)
        self.assertIn("NO USES ENCABEZADOS", source)
        self.assertIn("No menciones borradores ni revisiones", source)
        self.assertIn("BORRADOR PREVIO", source)
        self.assertIn("NOTAS DE MEJORA", source)
        self.assertIn("Sos la voz final de Fusion Reader v2", source)

    def legacy_dialogue_turn_text_answers_with_audio_without_touching_reader_tts_path(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_dialogue_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["transcript"], "¿Qué opinás del bloque?")
        self.assertEqual(out["answer"], "Entendido.")
        self.assertTrue(out["audio"])
        self.assertEqual(out["stt_ms"], 0)
        self.assertEqual(app.dialogue_status()["turns"], 2)

    def legacy_dialogue_turn_text_fast_ack_skips_tts(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_dialogue_ack = True
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Entendido.")
        self.assertEqual(out["provider"], "text_ack")
        self.assertEqual(out["tts_ms"], 0)
        self.assertEqual(out["audio"], "")
        self.assertEqual(provider.calls, [])

    def legacy_dialogue_stop_command_does_not_answer_again(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("detente")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_control")
        self.assertEqual(out["answer"], "")
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_turn_audio_uses_stt_provider(self):
        provider = NullTTSProvider()
        stt = NullSTTProvider("Hola laboratorio.")
        app = test_app(tts=provider, stt=stt)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["transcript"], "Hola laboratorio.")
        self.assertEqual(out["stt_provider"], "null_stt")
        self.assertIn("trace", out)
        self.assertIn("stt_wall_ms", out["trace"])
        self.assertIn("server_total_ms", out["trace"])

    def legacy_dialogue_empty_transcript_is_recoverable(self):
        app = test_app(stt=EmptyTranscriptSTTProvider())
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_stt")
        self.assertEqual(out["detail"], "empty_transcript")
        self.assertIn("No alcancé", out["answer"])
        self.assertEqual(out["provider"], "null")
        self.assertTrue(out["audio"])

    def legacy_dialogue_stt_failure_returns_human_answer_instead_of_silence(self):
        app = test_app(stt=BrokenSTTProvider())
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["error"], "transcription_failed")
        self.assertEqual(out["failed_stage"], "stt")
        self.assertEqual(out["stt_provider"], "broken_stt")
        self.assertIn("No pude entender bien el audio", out["answer"])
        self.assertTrue("audio" in out)

    def legacy_dialogue_chat_failure_returns_human_answer_and_trace(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.conversation = ConversationCore(FailingChatProvider())
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["failed_stage"], "chat")
        self.assertIn("Se cayó el diálogo local", out["answer"])
        self.assertIn("chat_ms", out["trace"])
        logged = (root / "dialogue_trace.jsonl").read_text(encoding="utf-8")
        self.assertIn('"human_error": "Se cay', logged)
        self.assertIn('"chat_provider": "ollama"', logged)

    def legacy_dialogue_turn_text_keeps_text_when_tts_fails(self):
        app = test_app(tts=FailingTTSProvider())
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Entendido.")
        self.assertFalse(out["voice_ok"])
        self.assertEqual(out["audio"], "")
        self.assertFalse(out["audio_available"])

    def legacy_dialogue_turn_text_defaults_to_neural_voice_not_browser_ack(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"], "null")
        self.assertTrue(out["audio"])
        self.assertEqual(len(provider.calls), 1)

    def legacy_dialogue_note_command_defaults_to_neural_voice_not_browser_ack(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("tomá nota de detalle hablado")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["provider"], "null")
        self.assertTrue(out["audio"])
        self.assertEqual(len(provider.calls), 1)

    def legacy_stt_filters_common_outro_hallucinations(self):
        self.assertTrue(is_hallucinated_transcript("¡Suscríbete!"))
        self.assertTrue(is_hallucinated_transcript("Suscríbete al canal"))
        self.assertTrue(is_hallucinated_transcript("Subtítulos realizados por la comunidad de Amara.org"))
        self.assertTrue(is_hallucinated_transcript("¡Giraff!"))
        self.assertFalse(is_hallucinated_transcript("quiero hacer una nota sobre la palabra suscríbete en el texto"))

    def legacy_dialogue_hallucinated_transcript_is_ignored_before_chat(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app(stt=HallucinatedTranscriptSTTProvider())
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertTrue(out["ignored"])
        self.assertEqual(out["model"], "reader_stt")
        self.assertEqual(out["detail"], "hallucinated_transcript")
        self.assertEqual(out["answer"], "")
        self.assertEqual(out["tts_ms"], 0)
        self.assertEqual(chat_provider.calls, [])

    def legacy_notes_persist_by_document_and_chunk(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.load_text("doc", "Doc", make_reading_document("Doc", 24), prefetch=False)
        created = app.create_note("Primera nota")
        self.assertTrue(created["ok"])
        self.assertEqual(created["note"]["chunk_number"], 1)
        app.jump(2)
        app.create_note("Segunda nota")
        reopened = test_app(root=root)
        self.assertEqual([item["text"] for item in reopened.list_notes(doc_id="doc")["items"]], ["Primera nota", "Segunda nota"])
        current_notes = app.list_notes(current_only=True)["items"]
        self.assertEqual(len(current_notes), 1)
        self.assertEqual(current_notes[0]["text"], "Segunda nota")

    def legacy_restart_restores_last_document_cursor_and_notes(self):
        root = Path(tempfile.mkdtemp())
        imported = root / "imported.txt"
        imported.write_text(make_reading_document("Importado", 24), encoding="utf-8")
        app = test_app(root=root)
        app.load_file(imported, prefetch=False)
        app.jump(2)
        created = app.create_note("Nota persistente")
        self.assertTrue(created["ok"])
        reopened = test_app(root=root)
        restored = reopened.status()
        self.assertEqual(restored["doc_id"], "imported")
        self.assertEqual(restored["current"], 2)
        notes = reopened.list_notes(current_only=True)["items"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["text"], "Nota persistente")

    def legacy_notes_update_delete_and_chat_command(self):
        app = test_app()
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("guardá esto como nota: revisar esta idea")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        note = out["note"]
        self.assertEqual(note["text"], "revisar esta idea")
        updated = app.update_note(note["note_id"], "idea editada")
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["note"]["text"], "idea editada")
        deleted = app.delete_note(note["note_id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(app.list_notes()["items"], [])

    def legacy_chat_note_without_document_becomes_laboratory_note(self):
        app = test_app()
        self.assertTrue(app.chat("hola")["ok"])
        out = app.chat("estamos haciendo pruebas, guarda una nota de nuestro saludo")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Nota guardada como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["anchor_number"], 1)
        self.assertEqual(out["note"]["doc_id"], "__laboratory__")
        notes = app.list_notes(doc_id="__laboratory__")["items"]
        self.assertEqual(len(notes), 1)
        self.assertIn("hola", notes[0]["quote"].lower())

    def legacy_chat_laboratory_reference_uses_l_note_even_with_document_loaded(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        self.assertTrue(app.chat("hola")["ok"])
        out = app.chat("estamos haciendo pruebas, guarda una nota de nuestro saludo")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Nota guardada como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def legacy_dialogue_reference_to_recent_reply_becomes_laboratory_note(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me escuchás?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("Tomá nota de esto que acabás de decir.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertIn("Entendido", out["note"]["quote"])
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def legacy_dialogue_stt_like_recent_speech_note_becomes_laboratory_note(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me he escuchado?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("Tomando a esto que acabo de decir. Tomando a lo que acabo de decir.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["text"], "Entendido.")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def legacy_dialogue_generic_eso_note_routes_to_laboratory(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me escuchás?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("sí, tome nota de eso")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["text"], "Entendido.")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def legacy_dialogue_short_stt_artifact_note_uses_recent_laboratory_content(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me escuchás?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("Toma nota D.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["text"], "Entendido.")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def legacy_notes_get_compact_labels_and_can_be_renamed(self):
        app = test_app()
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        created = app.create_note("la transformación del lenguaje humano")
        self.assertTrue(created["ok"])
        note = created["note"]
        self.assertEqual(note["label"], "transformación lenguaje humano")
        renamed = app.rename_note(note["note_id"], "lenguaje IA")
        self.assertTrue(renamed["ok"])
        self.assertEqual(renamed["note"]["label"], "lenguaje IA")
        updated = app.update_note(note["note_id"], "otro texto diferente")
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["note"]["label"], "lenguaje IA")

    def legacy_note_command_understands_take_note_language(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("necesitaría que tomes nota del giro estadístico del logos")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "giro estadístico del logos")
        self.assertEqual(chat_provider.calls, [])

    def legacy_note_command_understands_natural_document_notes_phrase(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("Hola, ¿puedes tomar notas en notas del documento que vamos a hablar de la transformación del lenguaje humano en el bloque 3?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "la transformación del lenguaje humano")
        self.assertEqual(chat_provider.calls, [])

    def legacy_note_request_without_content_does_not_reach_llm(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("¿puedes tomar notas?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["detail"], "missing_note_text")
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_command_answers_with_audio(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_note_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("guardá esto como nota: detalle hablado")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "detalle hablado")
        self.assertIn("guardé", out["answer"])
        self.assertTrue(out["audio"])

    def legacy_dialogue_note_command_succeeds_even_when_tts_fails(self):
        app = test_app(tts=FailingTTSProvider())
        app.fast_note_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("tomá nota de detalle sin voz")
        self.assertTrue(out["ok"])
        self.assertFalse(out["voice_ok"])
        self.assertEqual(out["note"]["text"], "detalle sin voz")

    def legacy_dialogue_note_command_fast_ack_skips_tts(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_note_ack = True
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("tomá nota de detalle rápido")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["provider"], "text_ack")
        self.assertEqual(out["tts_ms"], 0)
        self.assertEqual(provider.calls, [])
        self.assertIn("trace", out)
        self.assertIn("note_ms", out["trace"])

    def legacy_dialogue_note_command_allows_intro_and_stt_variant(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("Estamos en una prueba, así que tomad nota de esta defensa reconoce la inquietud filosófica para hablarlo después.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "esta defensa reconoce la inquietud filosófica para hablarlo después")
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_command_understands_save_the_note_phrase(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Idea 2: Ontología del lenguaje como cálculo probabilístico.", prefetch=False)
        out = app.dialogue_turn_text("me puedes guardar la nota de ontología del lenguaje como cálculo probabilístico para después")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["chunk_number"], 1)
        self.assertEqual(out["note"]["text"], "ontología del lenguaje como cálculo probabilístico para después")
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_command_understands_followup_save_without_note_word(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        app.dialogue_turn_text("guardá esto como nota: primera idea")
        out = app.dialogue_turn_text("y guarda también la segunda idea de este mismo bloque")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "la segunda idea de este mismo bloque")
        self.assertEqual(len(app.list_notes()["items"]), 2)
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_command_understands_make_me_a_note(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.dialogue_turn_text("haceme una nota de adaptación al interlocutor")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "adaptación al interlocutor")
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_command_understands_leave_a_note(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.dialogue_turn_text("Deja una nota de que tenemos que volver a este bloque")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "tenemos que volver a este bloque")
        self.assertEqual(len(app.list_notes(current_only=True)["items"]), 1)
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_command_saves_previous_long_phrase(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.dialogue_turn_text(
            "es ampliamente aceptada, pero un crítico podría materializar. "
            "No ocurre algo similar con los humanos en ciertas interacciones rutinarias o performáticas. "
            "Guarda eso en una nota."
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertIn("No ocurre algo similar con los humanos", out["note"]["text"])
        self.assertEqual(chat_provider.calls, [])

    def legacy_dialogue_note_uses_visible_chunk_index_from_client(self):
        app = test_app()
        app.load_text("doc", "Doc", make_reading_document("Doc", 36), prefetch=False)
        app.jump(3)
        out = app.dialogue_turn_text("tomá nota de esto corresponde al bloque dos", chunk_index=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["note"]["chunk_number"], 2)
        self.assertEqual(app.session.status()["current"], 3)

    def legacy_auto_stt_falls_back_when_primary_is_unavailable(self):
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        stt = AutoSTTProvider(
            primary=FasterWhisperServerSTTProvider(base_url="http://127.0.0.1:9", timeout_seconds=0.01),
            fallback=NullSTTProvider("Fallback listo."),
        )
        out = stt.transcribe_file(audio, mime="audio/webm")
        self.assertTrue(out.ok)
        self.assertEqual(out.text, "Fallback listo.")
        self.assertEqual(out.provider, "null_stt")

    def legacy_auto_stt_falls_back_when_primary_returns_empty_transcript(self):
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        stt = AutoSTTProvider(primary=EmptyTranscriptSTTProvider(), fallback=NullSTTProvider("Recuperado por fallback."))
        out = stt.transcribe_file(audio, mime="audio/webm")
        self.assertTrue(out.ok)
        self.assertEqual(out.text, "Recuperado por fallback.")
        self.assertEqual(out.provider, "null_stt")

    def legacy_whisper_cli_fallback_uses_known_homebrew_path(self):
        command = Path("/home/linuxbrew/.linuxbrew/bin/whisper")
        if not command.exists():
            self.skipTest("Homebrew whisper command is not installed on this host")
        previous = os.environ.get("FUSION_READER_STT_COMMAND")
        try:
            os.environ.pop("FUSION_READER_STT_COMMAND", None)
            provider = WhisperCliSTTProvider()
            self.assertEqual(provider.command, str(command))
            self.assertTrue(provider.health()["ok"])
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_STT_COMMAND", None)
            else:
                os.environ["FUSION_READER_STT_COMMAND"] = previous

    def legacy_auto_stt_does_not_fallback_for_hallucinated_primary(self):
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        fallback = NullSTTProvider("Fallback no debe usarse.")
        stt = AutoSTTProvider(primary=HallucinatedTranscriptSTTProvider(), fallback=fallback)
        out = stt.transcribe_file(audio, mime="audio/webm")
        self.assertFalse(out.ok)
        self.assertEqual(out.detail, "hallucinated_transcript")
        self.assertEqual(out.provider, "hallucinated_stt")
        self.assertEqual(fallback.calls, [])

    def legacy_read_current_times_out_stale_prefetch(self):
        app = test_app()
        app.prefetch_wait_seconds = 0.001
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        stale = Future()
        with app._prefetch_lock:
            app._prefetch_index = 0
            app._prefetch_future = stale
        out = app.read_current(play=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["detail"], "prefetch_timeout")

    def legacy_voice_metrics_are_persisted(self):
        root = Path(tempfile.mkdtemp())
        metrics = VoiceMetricsStore(root / "voice_metrics.jsonl")
        app = FusionReaderV2(tts=NullTTSProvider(), cache=AudioCache(root / "audio_cache"), metrics=metrics)
        app.load_text("doc", "Doc", "Uno.")
        app.read_current(play=False)
        recent = app.recent_voice_metrics()["items"]
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["event"], "read")
        self.assertEqual(recent[0]["doc_id"], "doc")
        self.assertIn("ready_ms", recent[0])

    def legacy_voice_metrics_summary_groups_by_provider(self):
        root = Path(tempfile.mkdtemp())
        app = FusionReaderV2(
            tts=NullTTSProvider(),
            cache=AudioCache(root / "audio_cache"),
            metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        )
        app.load_text("doc", "Doc", "Uno.")
        app.read_current(play=False)
        summary = app.voice_metrics_summary()["items"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["event"], "read")
        self.assertEqual(summary[0]["provider"], "null")
        self.assertEqual(summary[0]["count"], 1)
        self.assertIn("ready_ms_avg", summary[0])

    def legacy_voice_metrics_group_by_document_and_chunk(self):
        root = Path(tempfile.mkdtemp())
        app = FusionReaderV2(
            tts=NullTTSProvider(),
            cache=AudioCache(root / "audio_cache"),
            metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        )
        app.load_text("doc", "Doc", make_reading_document("Doc", 24), prefetch=False)
        app.read_current(play=False)
        app.next()
        app.read_current(play=False)
        docs = app.voice_metrics_by_document()["items"]
        chunks = app.voice_metrics_by_chunk(doc_id="doc")["items"]
        self.assertEqual(docs[0]["doc_id"], "doc")
        self.assertEqual(docs[0]["count"], 2)
        self.assertEqual({item["current"] for item in chunks}, {1, 2})

    def legacy_chat_gets_visible_chunk_and_full_document_without_tts(self):
        provider = NullTTSProvider()
        chat_provider = NullChatProvider("Veo el texto actual.")
        root = Path(tempfile.mkdtemp())
        app = FusionReaderV2(
            tts=provider,
            cache=AudioCache(root / "audio_cache"),
            metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
            conversation=ConversationCore(chat_provider),
        )
        app.load_text("doc", "Doc", "Pantalla actual.\n\nContexto posterior del documento.", prefetch=False)
        out = app.chat("¿Qué ves en pantalla?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Veo el texto actual.")
        self.assertEqual(len(provider.calls), 0)
        messages = chat_provider.calls[0][0]
        prompt = "\n".join(item["content"] for item in messages)
        self.assertIn("TEXTO EN PANTALLA:", prompt)
        self.assertIn("Pantalla actual.", prompt)
        self.assertIn("DOCUMENTO COMPLETO DISPONIBLE:", prompt)
        self.assertIn("Contexto posterior del documento.", prompt)

    def legacy_chat_context_includes_reference_documents(self):
        chat_provider = NullChatProvider("Veo el apoyo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.\n\nContexto del principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta sobre el mismo tema.\n\nComparación complementaria.")
        out = app.chat("Compará el principal con la consulta.")
        self.assertTrue(out["ok"])
        messages = chat_provider.calls[0][0]
        prompt = "\n".join(item["content"] for item in messages)
        self.assertIn("DOCUMENTOS DE CONSULTA:", prompt)
        self.assertIn("Consulta", prompt)
        self.assertIn("Comparación complementaria", prompt)

    def legacy_chat_lists_all_reference_documents_even_if_first_is_long(self):
        chat_provider = NullChatProvider("Los veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        long_text = " ".join(["analisis"] * 600)
        app.add_reference_text("ref-1", "Análisis Filosófico", long_text)
        app.add_reference_text("ref-2", "desgrabaciones.docx", "Primera desgrabación.\n\nSegunda desgrabación.")
        out = app.chat("¿Ves los documentos de consulta?")
        self.assertTrue(out["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Análisis Filosófico", prompt)
        self.assertIn("desgrabaciones.docx", prompt)

    def legacy_dialogue_context_includes_reference_document_intro_chunks(self):
        chat_provider = NullChatProvider("Sí, lo veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Bloque principal uno.\n\nBloque principal dos.", prefetch=False)
        app.add_reference_text(
            "desgrabaciones",
            "desgrabaciones.docx",
            "Primera línea de desgrabaciones.\n\nSegunda línea importante del documento.\n\nTercera línea.",
        )
        out = app.dialogue_turn_text("Dame un contexto general de desgrabaciones.docx.")
        self.assertTrue(out["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("desgrabaciones.docx", prompt)
        self.assertIn("Primera línea de desgrabaciones.", prompt)
        self.assertIn("Segunda línea importante del documento.", prompt)

    def legacy_chat_navigation_focuses_reference_block_without_replacing_main(self):
        app = test_app()
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            make_reading_sections(
                ("Consulta uno", "Primer bloque de consulta."),
                ("Consulta dos", "Dos consulta."),
                ("Consulta tres", "Tres consulta."),
            ),
        )
        out = app.chat("andá al bloque 2 de Desgrabaciones.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "focus_block")
        self.assertEqual(out["doc_id"], "ref")
        self.assertEqual(app.status()["doc_id"], "doc")
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)
        self.assertIn("Consulta dos", out["answer"])

    def legacy_chat_search_sets_laboratory_focus_on_match(self):
        app = test_app()
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            make_reading_sections(
                ("Consulta uno", "Primera parte."),
                ("Consulta dos", "Aca aparece YouTube como ejemplo pedagogico."),
                ("Consulta tres", "Cierre."),
            ),
        )
        out = app.chat("buscá dónde habla de YouTube en Desgrabaciones.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(out["current"], app.laboratory_focus_status()["chunk_number"])
        self.assertIn("YouTube", out["answer"])
        self.assertEqual(app.laboratory_focus_status()["query"], "YouTube")

    def legacy_chat_combined_focus_and_search_prefers_search_result_when_both_are_requested(self):
        app = test_app()
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            make_reading_sections(
                ("Consulta uno", "Speaker 1. Nada de YouTube aca."),
                ("Consulta dos", "YouTube aparece fuerte en este bloque."),
                ("Consulta tres", "Mas texto."),
            ),
        )
        out = app.chat("Andá al bloque 1 de Desgrabaciones.docx y buscá dónde habla de YouTube y ese bloque qué dice exactamente.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(out["current"], app.laboratory_focus_status()["chunk_number"])
        self.assertIn("YouTube", out["answer"])
        self.assertGreaterEqual(app.laboratory_focus_status()["chunk_number"], 1)

    def legacy_chat_explicit_external_research_uses_openclaw_bridge(self):
        chat_provider = NullChatProvider("No deberia usarse el LLM local.")
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Sali a investigar afuera sobre Fedro.\nHallazgos:\n- Tesis A.\nFuentes:\n- Universidad X | https://ejemplo.test/tesis-a",
                spoken_answer="Sali a investigar afuera sobre Fedro. Encontre una tesis relevante de la Universidad X.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
                query="Fedro en El banquete",
                summary="Encontre una tesis relevante de la Universidad X.",
                findings=["Tesis A."],
                sources=[{"title": "Universidad X", "url": "https://ejemplo.test/tesis-a", "note": "tesis doctoral"}],
            )
        )
        app = test_app(external_research=bridge)
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        out = app.chat("Buscá en internet algunas tesis de doctorado sobre la postura de Fedro en El banquete.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "gemini-2.5-flash")
        self.assertTrue(out["external_research"])
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(chat_provider.calls, [])
        self.assertIn("Fedro", out["answer"])
        self.assertEqual(out["external_sources"][0]["title"], "Universidad X")

    def legacy_chat_document_search_stays_local_even_when_bridge_exists(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="No deberia activarse.",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
            )
        )
        app = test_app(external_research=bridge)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            "Primera parte.\n\nAcá aparece YouTube como ejemplo pedagógico.\n\nCierre.",
        )
        out = app.chat("buscá dónde habla de YouTube en Desgrabaciones.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(len(bridge.calls), 0)

    def legacy_chat_normal_question_does_not_activate_external_research(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="No deberia activarse.",
                provider="searxng",
                model="searxng-local",
            )
        )
        chat_provider = NullChatProvider("Recuerdo el contexto actual del laboratorio.")
        app = test_app(external_research=bridge)
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        out = app.chat("¿Qué recordás del contexto actual del laboratorio?")
        self.assertTrue(out["ok"])
        self.assertFalse(out.get("external_research", False))
        self.assertEqual(len(bridge.calls), 0)
        self.assertTrue(chat_provider.calls)

    def legacy_chat_explicit_academic_search_activates_external_research(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Sali a investigar afuera sobre Diotima.\nFuentes:\n- Stanford | https://ejemplo.test/diotima",
                spoken_answer="Sali a investigar afuera sobre Diotima. Encontré una fuente relevante.",
                detail="external_research_ok",
                provider="searxng",
                model="searxng-local",
                query="Diotima y la escalera del amor",
                summary="Encontré una fuente relevante sobre Diotima.",
                sources=[{"title": "Stanford", "url": "https://ejemplo.test/diotima", "note": "entrada académica"}],
            )
        )
        app = test_app(external_research=bridge)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        out = app.chat("busca tesis o papers sobre Diotima y la escalera del amor")
        self.assertTrue(out["external_research"])
        self.assertEqual(out["provider"], "searxng")
        self.assertEqual(len(bridge.calls), 1)

    def legacy_chat_search_is_accent_insensitive(self):
        app = test_app()
        app.load_text("doc", "Principal", "Fedro habla con Socrates sobre eros.", prefetch=False)
        out = app.chat("buscá dónde aparece Sócrates en el documento")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_matches")
        self.assertIn("Socrates", out["answer"])

    def legacy_dialogue_search_no_matches_is_not_a_hard_failure(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Principal", "Fedro habla con Agatón.", prefetch=False)
        out = app.dialogue_turn_text("buscá dónde aparece Sócrates en el documento completo")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_no_matches")
        self.assertIn("No encontré coincidencias", out["answer"])
        self.assertTrue(provider.calls)

    def legacy_followup_chat_gets_laboratory_focus_in_context(self):
        chat_provider = NullChatProvider("Sí, sigo ese foco.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Bloque principal uno.\n\nBloque principal dos.", prefetch=False)
        app.add_reference_text("ref", "Desgrabaciones.docx", "Uno consulta.\n\nDos consulta con YouTube.\n\nTres consulta.")
        nav = app.chat("buscá YouTube en Desgrabaciones.docx")
        self.assertTrue(nav["ok"])
        followup = app.chat("¿y ese bloque qué plantea?")
        self.assertTrue(followup["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)
        self.assertIn("Desgrabaciones.docx", prompt)
        self.assertIn("Dos consulta con YouTube.", prompt)

    def legacy_dialogue_external_research_uses_bridge_and_keeps_urls_out_of_spoken_tts(self):
        provider = NullTTSProvider()
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Sali a investigar afuera sobre Fedro.\nFuentes:\n- Universidad X | https://ejemplo.test/tesis-a",
                spoken_answer="Sali a investigar afuera sobre Fedro. Encontre una tesis relevante de la Universidad X.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
                query="Fedro en El banquete",
                summary="Encontre una tesis relevante de la Universidad X.",
                findings=["La tesis ve a Fedro como una entrada elogiosa al eros."],
                sources=[{"title": "Universidad X", "url": "https://ejemplo.test/tesis-a", "note": "tesis doctoral"}],
            )
        )
        app = test_app(tts=provider, external_research=bridge)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("Buscá en la red tesis sobre Fedro en El banquete.")
        self.assertTrue(out["ok"])
        self.assertTrue(out["external_research"])
        self.assertEqual(out["model"], "gemini-2.5-flash")
        self.assertEqual(len(bridge.calls), 1)
        self.assertTrue(provider.calls)
        self.assertNotIn("https://", provider.calls[-1][0])

    def legacy_dialogue_external_research_keeps_text_when_tts_fails(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Encontré estas fuentes sobre Diotima.\nFuentes:\n- Fuente A | https://ejemplo.test/a",
                spoken_answer="Encontré estas fuentes sobre Diotima.",
                detail="external_research_ok",
                provider="searxng",
                model="searxng-local",
                sources=[{"title": "Fuente A", "url": "https://ejemplo.test/a", "note": "nota"}],
            )
        )
        app = test_app(tts=FailingTTSProvider(), external_research=bridge)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("busca afuera fuentes sobre Diotima y la escalera del amor")
        self.assertTrue(out["ok"])
        self.assertTrue(out["external_research"])
        self.assertEqual(out["model"], "searxng-local")
        self.assertIn("Fuentes:", out["answer"])
        self.assertFalse(out["voice_ok"])
        self.assertEqual(out["audio"], "")

    def legacy_status_reports_runtime_services_without_ambiguous_ok(self):
        app = test_app(stt=BrokenSTTProvider())
        app.conversation = ConversationCore(FailingChatProvider())
        app.set_reasoning_mode("supreme")
        status = app.status()
        dialogue = app.dialogue_status()
        self.assertIn("services", status)
        self.assertFalse(status["services"]["stt"]["ready"])
        self.assertFalse(status["services"]["chat"]["ready"])
        self.assertEqual(status["services"]["dialogue_reasoning"]["applied_mode"], "thinking")
        self.assertTrue(dialogue["dialogue_reasoning"]["degraded"])
        self.assertIn("external_research", dialogue)

    def legacy_chat_compare_uses_focus_and_explicit_target(self):
        app = test_app()
        app.load_text(
            "doc",
            "Principal",
            make_reading_sections(
                ("Principal uno", "Bloque principal uno."),
                ("Principal dos", "Bloque principal dos importante."),
                paragraphs_per_section=10,
            ),
            prefetch=False,
        )
        app.add_reference_text(
            "ref",
            "Análisis Filosófico.docx",
            make_reading_sections(
                ("Consulta uno", "Bloque uno consulta."),
                ("Consulta dos", "Bloque dos consulta importante."),
                ("Consulta tres", "Bloque tres consulta."),
            ),
        )
        nav = app.chat("andá al bloque 2 de Análisis Filosófico.docx")
        self.assertTrue(nav["ok"])
        out = app.chat("compará este bloque con el bloque 2 del principal")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_compare")
        self.assertEqual(out["detail"], "compare_blocks")
        self.assertIn("Comparación:", out["answer"])
        self.assertIn("Análisis Filosófico.docx", out["answer"])
        self.assertIn("Principal", out["answer"])

    def legacy_dialogue_compare_returns_reader_compare_without_llm(self):
        app = test_app()
        app.load_text("doc", "Principal", "Idea central del principal.\n\nSegunda idea del principal.", prefetch=False)
        app.add_reference_text("ref", "ideas.docx", "Idea central de consulta.\n\nSegunda idea de consulta.")
        app.chat("andá al bloque 1 de ideas.docx")
        out = app.dialogue_turn_text("compará este bloque con el bloque 1 del principal")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_compare")
        self.assertEqual(out["detail"], "compare_blocks")
        self.assertIn("Comparé", out["answer"])

    def legacy_dialogue_reflective_block_request_sets_focus_and_continues_with_llm(self):
        chat_provider = NullChatProvider("Lucy piensa el bloque con vuelo propio.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text(
            "ref",
            "ideas.docx",
            make_reading_sections(
                ("Ideas uno", "Primer bloque de consulta."),
                ("Ideas dos", "Bloque 2: la estadística del lenguaje revela un régimen de inteligibilidad."),
                ("Ideas tres", "Tercer bloque."),
            ),
        )
        out = app.dialogue_turn_text("Quiero que pensemos filosóficamente sobre el bloque 2 de ideas.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "null")
        self.assertNotEqual(out["detail"], "focus_block")
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)
        self.assertEqual(app.laboratory_focus_status()["title"], "ideas.docx")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)
        self.assertIn("ideas.docx", prompt)
        self.assertIn("Ideas dos", prompt)

    def legacy_chat_uses_recent_laboratory_text_without_document(self):
        chat_provider = NullChatProvider("Sí, lo veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        pasted = "Texto pegado en laboratorio: la estadística reemplaza al logos como régimen de verdad."
        first = app.chat(pasted)
        second = app.chat("¿Ves lo que acabo de poner?")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(len(chat_provider.calls), 2)
        messages = chat_provider.calls[1][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("MATERIAL RECIENTE DEL LABORATORIO:", joined)
        self.assertIn("la estadística reemplaza al logos", joined)
        self.assertIn("usa el historial reciente del laboratorio", messages[0]["content"])
        self.assertIn("menciona brevemente el contenido reciente", messages[0]["content"])

    def legacy_dialogue_context_does_not_send_full_document(self):
        chat_provider = NullChatProvider("Respuesta breve.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text(
            "doc",
            "Doc",
            make_reading_sections(
                ("Bloque uno", "Bloque uno."),
                ("Bloque dos", "Bloque dos visible."),
                ("Bloque tres", "Bloque tres final."),
            ),
            prefetch=False,
        )
        app.jump(2)
        out = app.dialogue_turn_text("¿Qué te parece?")
        self.assertTrue(out["ok"])
        messages = chat_provider.calls[0][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("Bloque dos visible", joined)
        self.assertNotIn("DOCUMENTO COMPLETO DISPONIBLE", joined)
        self.assertIn("No digas que guardaste notas", messages[0]["content"])

    def legacy_dialogue_uses_recent_text_chat_laboratory_material(self):
        chat_provider = NullChatProvider("Lo tengo presente.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        pasted = "Programa por problemas: verdad, lenguaje, poder y cuerpo para sexto año."
        chat_out = app.chat(pasted)
        dialogue_out = app.dialogue_turn_text("¿Estás leyendo lo que pegué en el laboratorio?")
        self.assertTrue(chat_out["ok"])
        self.assertTrue(dialogue_out["ok"])
        self.assertEqual(len(chat_provider.calls), 2)
        messages = chat_provider.calls[1][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("MATERIAL RECIENTE DEL LABORATORIO:", joined)
        self.assertIn("Programa por problemas", joined)
        self.assertIn("no digas que no ves texto", messages[0]["content"])

    def legacy_clear_laboratory_history_removes_chat_and_dialogue_context(self):
        chat_provider = NullChatProvider("Lo tengo presente.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        pasted = "Programa por problemas: verdad, lenguaje, poder y cuerpo para sexto año."
        self.assertTrue(app.chat(pasted)["ok"])
        self.assertTrue(app.dialogue_turn_text("¿Leés lo que pegué?")["ok"])
        cleared = app.clear_laboratory_history()
        self.assertTrue(cleared["ok"])
        self.assertGreaterEqual(cleared["chat_items"], 2)
        self.assertGreaterEqual(cleared["dialogue_items"], 2)
        self.assertTrue(app.dialogue_turn_text("¿Leés lo que pegué?")["ok"])
        messages = chat_provider.calls[-1][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertNotIn("MATERIAL RECIENTE DEL LABORATORIO:", joined)
        self.assertNotIn("Programa por problemas", joined)

    def legacy_server_exposes_laboratory_history_reset_button_and_endpoint(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("clearLabHistoryBtn", server)
        self.assertIn("/api/laboratory/reset", server)
        self.assertIn("Historial de laboratorio borrado", server)

    def legacy_server_exposes_reference_documents_ui_and_endpoints(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("referenceModeToggle", server)
        self.assertIn("Documentos de consulta", server)
        self.assertIn("/api/reference/promote", server)
        self.assertIn("/api/reference/remove", server)
        self.assertIn("Agregar como consulta", server)
        self.assertIn("labFocus", server)
        self.assertIn("Foco del laboratorio", server)
        self.assertNotIn("refreshStatus(", server)

    def legacy_server_upload_ui_accepts_dotx_like_backend(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn(".dotx", server)
        self.assertIn(".docm", server)
        self.assertIn(".pages", server)
        self.assertIn("DOCX/DOTX", server)

    def legacy_server_distinguishes_laboratory_notes_with_l_prefix(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("const LAB_NOTES_DOC_ID = '__laboratory__';", server)
        self.assertIn("return `L${Number(note && note.anchor_number || 1)}`;", server)
        self.assertIn("Notas del laboratorio", server)
        self.assertIn("Promise.all([", server)

    def legacy_manual_chat_uses_dialogue_voice_when_dialogue_is_active(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("async function sendTypedDialogue(message)", server)
        self.assertIn("if (dialogue.active)", server)
        self.assertIn("api('/api/dialogue/turn', { text: message", server)
        self.assertIn("await playDialogueAnswer(data)", server)

    def legacy_reasoning_tabs_and_endpoint_exist_in_server_ui(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("Pensamiento supremo", server)
        self.assertIn("reasoningNormalBtn", server)
        self.assertIn("api('/api/reasoning/mode', { mode: targetMode })", server)
        self.assertIn("Supremo pedido; diálogo usa Pensamiento para cuidar latencia.", server)

    def legacy_dialogue_low_latency_defaults_are_configured(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        stt_server = Path("scripts/fusion_reader_v2_stt_server.py").read_text(encoding="utf-8")
        self.assertIn("silenceStopMs: 1250", server)
        self.assertIn("speechStartMs: 35", server)
        self.assertIn("createScriptProcessor(4096, 1, 1)", server)
        self.assertIn("encodeDialogueWav(dialogue.pcmChunks, dialogue.sampleRate)", server)
        self.assertIn("filename: 'dialogue.wav'", server)
        self.assertNotIn("new MediaRecorder", server)
        self.assertNotIn("requestData()", server)
        self.assertIn('FUSION_READER_STT_BEAM_SIZE", "1"', stt_server)
        self.assertIn('FUSION_READER_STT_RECOVERY_BEAM_SIZE', stt_server)
        self.assertIn("STT convert_failed", stt_server)

    def legacy_server_exposes_free_laboratory_mode_button_and_endpoint(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("freeModeBtn", server)
        self.assertIn("/api/laboratory/mode", server)
        self.assertIn("Modo libre", server)
        self.assertIn("Documento disponible:", server)
        self.assertIn("Sin documento activo", server)

    def legacy_dialogue_barge_in_keeps_pre_roll_for_short_commands(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("const interruptedWhileSpeech = dialogue.bargeInSpeechMs > 0;", server)
        self.assertIn("dialogue.speechMs = Math.max(dialogue.speechMs, dialogue.speechStartMs);", server)
        self.assertIn("dialogue.suppressUntil = performance.now() + 40;", server)

    def legacy_academic_profile_uses_larger_token_budget(self):
        academic = Path("scripts/start_fusion_reader_v2_academic.sh").read_text(encoding="utf-8")
        launcher = Path("/home/lucy-ubuntu/.local/bin/fusion-reader-launcher").read_text(encoding="utf-8")
        self.assertIn('FUSION_READER_CHAT_NUM_PREDICT:-1536', academic)
        self.assertIn('FUSION_READER_CHAT_NUM_PREDICT:-1536', launcher)

    def legacy_ollama_thinking_default_token_budget_is_not_tiny(self):
        previous_think = os.environ.get("FUSION_READER_CHAT_THINK")
        previous_predict = os.environ.get("FUSION_READER_CHAT_NUM_PREDICT")
        try:
            os.environ["FUSION_READER_CHAT_THINK"] = "1"
            os.environ.pop("FUSION_READER_CHAT_NUM_PREDICT", None)
            provider = OllamaChatProvider(base_url="http://example.invalid")
            self.assertGreaterEqual(provider.num_predict, 1024)
        finally:
            if previous_think is None:
                os.environ.pop("FUSION_READER_CHAT_THINK", None)
            else:
                os.environ["FUSION_READER_CHAT_THINK"] = previous_think
            if previous_predict is None:
                os.environ.pop("FUSION_READER_CHAT_NUM_PREDICT", None)
            else:
                os.environ["FUSION_READER_CHAT_NUM_PREDICT"] = previous_predict

    def legacy_dialogue_shortener_closes_cut_text_without_ellipsis(self):
        app = test_app()
        app.dialogue_tts_max_chars = 90
        text = " ".join(["esta respuesta larga necesita cerrar sin quedarse colgada"] * 8)
        shortened = app._shorten_dialogue_answer(text)
        self.assertLessEqual(len(shortened), 91)
        self.assertTrue(shortened.endswith("."))
        self.assertNotIn("...", shortened)

    def legacy_import_plain_text_document(self):
        doc = import_document_bytes("cuento.txt", "Uno.\n\nDos.".encode("utf-8"))
        self.assertEqual(doc.doc_id, "cuento")
        self.assertEqual(doc.source_type, "text")
        self.assertIn("Dos.", doc.text)

    def legacy_import_docx_document(self):
        root = Path(tempfile.mkdtemp())
        path = root / "cuento.docx"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Primer parrafo.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Segundo parrafo.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("word/document.xml", xml)
        doc = import_document_bytes("cuento.docx", path.read_bytes())
        self.assertEqual(doc.source_type, "docx")
        self.assertIn("Primer parrafo.", doc.text)
        self.assertIn("Segundo parrafo.", doc.text)

    def legacy_import_odt_document(self):
        root = Path(tempfile.mkdtemp())
        path = root / "cuento.odt"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text>
    <text:p>Linea uno.</text:p>
    <text:p>Linea dos.</text:p>
  </office:text></office:body>
</office:document-content>"""
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("content.xml", xml)
        doc = import_document_bytes("cuento.odt", path.read_bytes())
        self.assertEqual(doc.source_type, "odt")
        self.assertIn("Linea uno.", doc.text)
        self.assertIn("Linea dos.", doc.text)

    def legacy_ocr_text_keeps_headings_and_paragraphs(self):
        raw = """Capítulo 1
Introducción

Este es un párrafo de prueba con suficiente texto normal para ser conservado.
Sigue en otra línea y mantiene la misma idea.
"""
        text = structured_plain_ocr_text(raw)
        self.assertIn("# Capítulo 1", text)
        self.assertIn("## Introducción", text)
        self.assertIn("Este es un párrafo", text)

    def legacy_clean_heading_preserves_chapter_number(self):
        self.assertEqual(clean_heading("Capítulo 1"), "Capítulo 1")
        self.assertEqual(clean_heading("## = Introducción >"), "Introducción")

    def legacy_repair_ocr_spacing_fixes_common_scan_merges(self):
        text = repair_ocr_spacing("Elabad llegó en elaño nuevo y miró alanciano delazul.")
        self.assertIn("El abad", text)
        self.assertIn("el año", text)
        self.assertIn("al anciano", text)
        self.assertIn("del azul", text)

    def legacy_lucy_profiles_academica_and_bohemia(self):
        from unittest.mock import patch
        chat_provider = NullChatProvider("Respuesta de prueba.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        
        # Default is academica
        self.assertEqual(app.profile_status()["mode"], "academica")
        app.load_text("doc", "Doc", "Contexto.", prefetch=False)
        app.chat("Hola")
        # Verificar que la persona academica esta en el prompt
        system_prompt = chat_provider.calls[-1][0][0]["content"]
        self.assertIn("Lucy Cunningham", system_prompt)
        self.assertIn("lectora crítica, rigurosa", system_prompt)
        self.assertIn("pensamiento crítico como rigor", system_prompt)
        
        # Switch to bohemia
        app.set_profile("bohemia")
        self.assertEqual(app.profile_status()["mode"], "bohemia")
        app.chat("Hola de nuevo")
        # Verificar que la persona bohemia esta en el prompt
        system_prompt = chat_provider.calls[-1][0][0]["content"]
        self.assertIn("Lucy Bohemia", system_prompt)
        self.assertIn("salvaje, libre y directa", system_prompt)
        self.assertIn("humanismo barato", system_prompt)
        self.assertNotIn("lectora crítica, rigurosa", system_prompt)
        
        # Verify model selection
        with patch.dict(os.environ, {"FUSION_READER_BOHEMIA_CHAT_MODEL": "bohemia-model:latest"}):
            app.chat("Test model")
            self.assertEqual(chat_provider.calls[-1][1], "bohemia-model:latest")

    def legacy_persona_overlay_length_and_independence(self):
        chat_provider = NullChatProvider("Respuesta.")
        core = ConversationCore(chat_provider)
        
        academica_overlay = core._persona_overlay(reasoning_mode="thinking", dialogue=True, profile="academica", free_mode=False)
        bohemia_overlay = core._persona_overlay(reasoning_mode="pensamiento_critico", dialogue=False, profile="bohemia", free_mode=True)
        
        self.assertLess(len(academica_overlay), 3000)
        self.assertLess(len(bohemia_overlay), 3000)
        
        # Verificar anclaje
        self.assertIn("Responde anclada al documento", academica_overlay)
        self.assertIn("modo libre", bohemia_overlay)
        
        # Verificar razonamiento independiente del perfil
        self.assertIn("con más calma", academica_overlay)
        self.assertIn("tensión dialéctica", bohemia_overlay)

    def legacy_server_ui_contains_profile_and_veil_selectors(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('id="profileSelect"', text)
        self.assertIn('id="veilSelect"', text)
        self.assertIn("setProfileMode(", text)
        self.assertIn("setVeilMode(", text)
        self.assertIn("/api/profile", text)
        self.assertIn("/api/veil", text)

    def legacy_start_fusion_reader_v2_bohemia_script_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        script_path = root / "scripts" / "start_fusion_reader_v2_bohemia.sh"
        self.assertTrue(script_path.exists())
        text = script_path.read_text(encoding="utf-8")
        self.assertIn("FUSION_READER_BOHEMIA_CHAT_MODEL", text)
        self.assertIn("huihui_ai/qwen3-abliterated:14b-v2-q8_0", text)
        self.assertIn("start_fusion_reader_v2.sh", text)
        self.assertNotIn("FUSION_READER_CHAT_MODEL=", text)

    def legacy_veil_overlay_is_applied_to_prompt(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        
        # Test nocturna
        app.set_veil("nocturna")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("madrugada", prompt)
        
        # Test evocadora
        app.set_veil("evocadora")
        app.chat("Hola")
        prompt_evocadora = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("imagen precisa", prompt_evocadora)
        self.assertIn("nervio conceptual", prompt_evocadora)
        self.assertNotIn("poetica", prompt_evocadora.lower())

    def legacy_directa_veil_is_sharp(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("directa")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("seco y frontal", prompt)
        self.assertIn("sin adornos", prompt)

    def legacy_desarme_veil_is_mechanical(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("desarme")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("mecanismo", prompt)
        self.assertIn("seduce", prompt)

    def legacy_lucy_veil_is_neutral(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("lucy")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        # Lucy (neutral) doesn't add extra veil prompt beyond base identity
        self.assertNotIn("nocturna", prompt)
        self.assertNotIn("evocadora", prompt)
        self.assertNotIn("directa", prompt)

    def legacy_bohemia_persona_contains_narrative_rein(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        # Set profile to bohemia
        app.set_profile("bohemia")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("intensidad en loop", prompt)
        self.assertIn("sombra en pose", prompt)
        self.assertIn("no cargados", prompt)

    def legacy_free_mode_without_document_request_excludes_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Lucy, ¿qué es la realidad?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertNotIn("Mancha", prompt)
        self.assertIn("Estás en modo libre", prompt)

    def legacy_free_mode_with_document_request_includes_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Según el documento, ¿dónde ocurre la acción?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Mancha", prompt)

    def legacy_document_mode_always_includes_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        app.set_laboratory_mode("document")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Mancha", prompt)

    def legacy_document_mode_literal_request_injects_literal_instruction(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "Doc", "Bloque uno: La realidad parece una costumbre compartida.", prefetch=False)
        app.set_laboratory_mode("document")
        app.chat("¿Qué dice el bloque actual?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("respuesta literal sobre el texto actual", prompt)
        self.assertIn("No saltes directo a interpretación", prompt)

    def legacy_document_mode_interpretation_request_does_not_force_literal_instruction(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "Doc", "Bloque uno: La realidad parece una costumbre compartida.", prefetch=False)
        app.set_laboratory_mode("document")
        app.chat("¿Qué significa el bloque actual?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertNotIn("respuesta literal sobre el texto actual", prompt)
        self.assertNotIn("No saltes directo a interpretación", prompt)

    def legacy_document_mode_mixed_literal_and_interpretation_request_orders_both(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "Doc", "Bloque uno: La realidad parece una costumbre compartida.", prefetch=False)
        app.set_laboratory_mode("document")
        app.chat("Leeme el bloque y después decime qué significa.")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("respuesta literal sobre el texto actual", prompt)
        self.assertIn("Primero reproduce o parafrasea fielmente", prompt)
        self.assertIn("Después agregá una interpretación breve y secundaria", prompt)

    def legacy_free_mode_without_document_request_does_not_inject_literal_document_instruction(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "Doc", "Bloque uno: La realidad parece una costumbre compartida.", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Qué es la realidad?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertNotIn("respuesta literal sobre el texto actual", prompt)

    def legacy_free_mode_explicit_document_literal_request_can_inject_literal_instruction(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "Doc", "Bloque uno: La realidad parece una costumbre compartida.", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Según el texto en pantalla, qué dice?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("respuesta literal sobre el texto actual", prompt)
        self.assertIn("Bloque uno: La realidad parece una costumbre compartida.", prompt)

    def legacy_supreme_mode_in_free_mode_honors_independence(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        app.set_laboratory_mode("free")
        app.set_reasoning_mode("supreme")
        app.chat("Háblame del tiempo.")
        # In supreme mode, we have draft, review, and final passes.
        # We check the first pass (draft)
        draft_prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertNotIn("Mancha", draft_prompt)
        # We check the final pass (synthesis) - it uses _messages_as_text which should also exclude it
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[2][0])
        self.assertNotIn("Mancha", final_prompt)

    def legacy_clear_document_resets_state(self):
        app = test_app()
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        self.assertEqual(app.session.status()["title"], "El Quijote")
        
        status = app.clear_document()
        self.assertEqual(status["title"], "")
        self.assertEqual(status["total"], 0)
        self.assertEqual(status["doc_id"], "")
        self.assertEqual(app.session.document, None)

    def legacy_server_contains_clear_document_button_and_endpoint(self):
        import scripts.fusion_reader_v2_server as server
        self.assertIn('id="clearDocBtn"', server.INDEX_HTML)
        self.assertIn('function clearDocument()', server.INDEX_HTML)
        # Check endpoint existence in do_POST logic via reflection or simple string check in script content
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "fusion_reader_v2_server.py")
        with open(script_path, "r") as f:
            content = f.read()
            self.assertIn('/api/document/clear', content)

    def legacy_closing_discipline_is_applied_by_default(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("lucy")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("No cierres por defecto con una pregunta", prompt)
        self.assertIn("Cerrá normalmente con una afirmación completa", prompt)

    def legacy_closing_discipline_is_strict_in_dialogue(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        # Check overlay directly
        prompt = app.conversation._persona_overlay(veil="lucy", dialogue=True)
        self.assertIn("no sostengas artificialmente la conversación con preguntas finales", prompt)

    def legacy_pregunta_viva_veil_omits_closing_discipline(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("pregunta_viva")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertNotIn("No cierres por defecto con una pregunta", prompt)
        self.assertIn("Cerrá con una pregunta que deje la idea abierta", prompt)

    def legacy_debate_veil_is_not_forcing_question_as_routine(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("debate")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("si hace falta, cerrá con una pregunta real, no automática", prompt)
        self.assertNotIn("devolvé una pregunta", prompt)

    def legacy_thinking_mode_does_not_force_questions(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Abrí preguntas solo si son realmente necesarias", prompt)
        self.assertNotIn("hacé preguntas necesarias", prompt)

    def legacy_voice_catalog_returns_available_voices(self):
        class VoiceTTS(NullTTSProvider):
            def voices(self):
                return ["voice1.wav", "voice2.wav"]
        app = test_app(tts=VoiceTTS())
        app.voice.voice = "voice1.wav"
        catalog = app.get_voice_catalog()
        self.assertTrue(catalog["ok"])
        self.assertEqual(catalog["current"], "voice1.wav")
        self.assertEqual(catalog["voices"], ["voice1.wav", "voice2.wav"])

    def legacy_set_voice_updates_state_and_persists(self):
        root = Path(tempfile.mkdtemp())
        class VoiceTTS(NullTTSProvider):
            def voices(self):
                return ["female_03.wav", "new_voice.wav"]
        app = test_app(tts=VoiceTTS(), root=root)
        app.voice.voice = "female_03.wav"
        out = app.set_voice("new_voice.wav")
        self.assertTrue(out["ok"])
        self.assertEqual(app.voice.voice, "new_voice.wav")
        
        # Verify persistence
        reopened = test_app(tts=VoiceTTS(), root=root)
        self.assertEqual(reopened.voice.voice, "new_voice.wav")

    def legacy_set_voice_cancels_prefetch_and_running_prepare(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.\n\nDos.", prefetch=True)
        # Mock prefetch future
        with app._prefetch_lock:
            future = Future()
            app._prefetch_futures[1] = future
        
        app.prepare_document() # Start preparation
        # Wait a bit for it to start
        for _ in range(20):
            if app.prepare_status()["status"] == "running":
                break
            time.sleep(0.01)
        
        self.assertEqual(app.prepare_status()["status"], "running")
        
        app.set_voice("female_01.wav")
        
        # Verify prefetch cleared
        self.assertEqual(len(app._prefetch_futures), 0)
        self.assertTrue(future.cancelled())
        # Verify prepare canceled (it goes to idle when finished/canceled)
        # wait a bit for thread to exit
        for _ in range(20):
            if app.prepare_status()["status"] != "running":
                break
            time.sleep(0.01)
        self.assertNotEqual(app.prepare_status()["status"], "running")


    def legacy_server_ui_contains_friendly_voice_labels(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("Voces M", text)
        self.assertIn("Voces V", text)
        self.assertIn("M03 — Hera", text)
        self.assertIn("M08 — Perséfone", text)
        self.assertIn("M09 — Hécate", text)
        self.assertIn("V01 — Zeus", text)
        self.assertIn("V06 — Hermes", text)
        self.assertIn("V11 — Hércules", text)
        self.assertIn("voiceColor", text)
        self.assertIn("voiceSortKey", text)
        self.assertIn("opt.value = v", text)
        self.assertIn("opt.textContent", text)
        # Should NOT contain old labels
        self.assertNotIn("Mujer 03 — Emilia", text)
        self.assertNotIn("Varón 01 — Bruno", text)
        self.assertNotIn("Especial — Morgan Freeman", text)
        self.assertNotIn("Voces especiales", text)

    def legacy_safe_output_name_strips_weird_input_and_keeps_docx_suffix(self):
        out = safe_output_name("../hola rara?.pdf")
        self.assertEqual(Path(out).name, out)
        self.assertTrue(out.endswith(".docx"))
        self.assertNotIn("..", out)
        self.assertIn("hola_rara", out)

    def legacy_find_downloads_dir_prefers_descargas_then_downloads_then_safe_fallback(self):
        with mock.patch("fusion_reader_v2.pdf_to_docx.Path.home", return_value=Path("/tmp/fake-home")):
            with mock.patch("fusion_reader_v2.pdf_to_docx.Path.exists", autospec=True, side_effect=lambda path: str(path).endswith("/Descargas")):
                self.assertEqual(find_downloads_dir(), Path("/tmp/fake-home/Descargas"))
            with mock.patch("fusion_reader_v2.pdf_to_docx.Path.exists", autospec=True, side_effect=lambda path: str(path).endswith("/Downloads")):
                self.assertEqual(find_downloads_dir(), Path("/tmp/fake-home/Downloads"))
            with mock.patch("fusion_reader_v2.pdf_to_docx.Path.exists", autospec=True, return_value=False):
                self.assertEqual(find_downloads_dir(), Path("/tmp/fake-home/Descargas"))

    def legacy_pdf_to_docx_conversion_creates_real_docx_with_text(self):
        root = Path(tempfile.mkdtemp())
        pdf_path = root / "probe.pdf"
        docx_path = root / "probe_convertido.docx"
        pdf_path.write_bytes(make_simple_pdf_bytes(["Capitulo 1", "La realidad parece una costumbre compartida."]))
        result = convert_pdf_to_docx(pdf_path, docx_path)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(docx_path.exists())
        with zipfile.ZipFile(docx_path) as zf:
            document_xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        self.assertIn("Capítulo 1", document_xml)
        self.assertIn("La realidad parece una costumbre compartida.", document_xml)

    def legacy_pdf_to_word_ui_is_compact_and_correct(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('id="pdfToWordTool"', server)
        self.assertIn('PDF → Word', server)
        # Should not have long descriptive text anymore
        self.assertNotIn("Soltá un PDF o hacé click para convertir", server)

    def legacy_server_pdf_to_word_limit_is_500mb(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('max_bytes: int = 500 * 1024 * 1024', server)
        self.assertIn('Límite: {max_bytes // (1024 * 1024)} MB.', server)

    @mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=False)
    @mock.patch("fusion_reader_v2.pdf_to_docx._ocr_pdf_pages")
    def legacy_pdf_to_word_ocr_fallback_logic(self, mock_ocr, mock_gpu):
        from fusion_reader_v2.pdf_to_docx import convert_pdf_to_docx
        # If no Docling GPU and it's a scan, it should now return error
        with mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=["   ", "  "]), \
             mock.patch("fusion_reader_v2.pdf_to_docx._page_count", return_value=2):
            
            res = convert_pdf_to_docx("dummy.pdf", "output.docx")
            self.assertFalse(res.ok)
            self.assertIn("Motor Docling GPU no disponible", res.error)

    @mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=False)
    def legacy_pdf_to_word_job_progress(self, mock_gpu):
        from fusion_reader_v2.pdf_to_docx import convert_pdf_to_docx, JobStatus
        from unittest.mock import patch

        job = JobStatus(job_id="test_job")
        progress_calls = []
        def callback(j):
            progress_calls.append((j.stage, j.current_page))

        # Use a string longer than 12 alpha chars
        with mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=["Este es un texto digital suficientemente largo."]), \
             mock.patch("fusion_reader_v2.pdf_to_docx._write_minimal_docx"), \
             mock.patch("fusion_reader_v2.pdf_to_docx._page_count", return_value=1):
            
            convert_pdf_to_docx("dummy.pdf", "output.docx", status_callback=callback, job=job)
            
            self.assertEqual(job.state, "done")
            self.assertEqual(job.total_pages, 1)
            stages = [c[0] for c in progress_calls]
            self.assertIn("preflight", stages)
            self.assertIn("extract_text", stages)
            self.assertIn("build_docx", stages)

    def legacy_pdf_to_word_docling_gpu_selection(self):
        from fusion_reader_v2.pdf_to_docx import convert_pdf_to_docx, JobStatus
        import fusion_reader_v2.pdf_to_docx as pdf_to_docx
        from unittest.mock import patch, MagicMock

        # Mock is_docling_gpu_available to True
        with mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=True):
            with mock.patch("fusion_reader_v2.pdf_to_docx._convert_with_docling_gpu") as mock_conv:
                mock_conv.return_value = MagicMock(ok=True, engine="docling_gpu")
                job = JobStatus(job_id="test_gpu")
                res = convert_pdf_to_docx("dummy.pdf", "dummy.docx", job=job)
                self.assertTrue(res.ok)
                self.assertEqual(res.engine, "docling_gpu")
                mock_conv.assert_called_once()

    def legacy_pdf_to_word_no_docling_gpu_fallback_for_scans(self):
        from fusion_reader_v2.pdf_to_docx import convert_pdf_to_docx, JobStatus
        import fusion_reader_v2.pdf_to_docx as pdf_to_docx
        from unittest.mock import patch

        # Mock is_docling_gpu_available to False
        # Mock _extract_pages_text to return empty text (scanned)
        with mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=False):
            with mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=["", ""]):
                job = JobStatus(job_id="test_no_gpu")
                res = convert_pdf_to_docx("dummy.pdf", "dummy.docx", job=job)
                self.assertFalse(res.ok)
                self.assertIn("Motor Docling GPU no disponible", res.error)

    def legacy_md_to_docx_sanitization_v2(self):
        from fusion_reader_v2.md_to_docx import sanitize_markdown
        
        # 1. Images and Placeholders
        md = "# Titulo\n![Image](data:image/png;base64,ABC)\n<!-- image -->\n福\n"
        sanitized = sanitize_markdown(md)
        self.assertNotIn("data:image", sanitized)
        self.assertNotIn("![Image]", sanitized)
        self.assertNotIn("<!-- image -->", sanitized)
        self.assertNotIn("福", sanitized)
        
        # 2. OCR Corrections
        md_ocr = "Hrs Magica y la CuartaEdicion. Detectos y Hechbizos."
        sanitized_ocr = sanitize_markdown(md_ocr)
        self.assertIn("Ars Magica", sanitized_ocr)
        self.assertIn("Cuarta Edición", sanitized_ocr)
        self.assertIn("Defectos", sanitized_ocr)
        self.assertIn("Hechizos", sanitized_ocr)
        
        # 3. Glued Words
        md_glued = "QueDios nos ayude enel viaje mimaestra."
        sanitized_glued = sanitize_markdown(md_glued)
        self.assertIn("Que Dios", sanitized_glued)
        self.assertIn("en el", sanitized_glued)
        self.assertIn("mi maestra", sanitized_glued)
        
        # 4. Punctuation Spacing
        md_punct = "Hola,mundo.Esto;está:pegado."
        sanitized_punct = sanitize_markdown(md_punct)
        self.assertIn("Hola, mundo", sanitized_punct)
        self.assertIn("mundo. Esto", sanitized_punct)
        self.assertIn("está: pegado", sanitized_punct)

    def legacy_md_to_docx_glued_words(self):
        from fusion_reader_v2.md_to_docx import sanitize_markdown
        
        # 1. Frequent patterns
        md = "Desarrollo, ediciony maqueta. Diariode Antoninus. todoslos Magos."
        sanitized = sanitize_markdown(md)
        self.assertIn("edición y", sanitized)
        self.assertIn("Diario de", sanitized)
        self.assertIn("todos los", sanitized)
        
        # 2. Long spans
        md_long = "Que Dios Majestuosoqueha observadotodamivida."
        sanitized_long = sanitize_markdown(md_long)
        self.assertIn("Majestuoso que ha", sanitized_long)
        self.assertIn("observado toda mi vida", sanitized_long)
        
        # 3. CamelCase separation
        md_camel = "QueDios bendiga Mimaestra y Nuestroviaje."
        sanitized_camel = sanitize_markdown(md_camel)
        self.assertIn("Que Dios", sanitized_camel)
        self.assertIn("Mi maestra", sanitized_camel)
        self.assertIn("Nuestro viaje", sanitized_camel)
        
        # 4. Protected terms (should NOT be split)
        md_prot = "Bonisagus y Bjornaer son Quaesitores que usan Intellego."
        sanitized_prot = sanitize_markdown(md_prot)
        self.assertIn("Bonisagus", sanitized_prot)
        self.assertIn("Bjornaer", sanitized_prot)
        self.assertIn("Quaesitores", sanitized_prot)
        self.assertIn("Intellego", sanitized_prot)
        self.assertNotIn("Bonisa gus", sanitized_prot)
        self.assertNotIn("Bjorna er", sanitized_prot)

    def legacy_md_to_docx_glued_words_v4_real_ars_magica_examples(self):
        from fusion_reader_v2.md_to_docx import detect_suspicious_glued_tokens, repair_glued_words_v4, sanitize_markdown

        samples = {
            "Diariode Antoninus": "Diario de Antoninus",
            "Desarrollodela Cuarta Edicionen": "Desarrollo de la Cuarta Edición en",
            "Coordinacion delaspruebas": "Coordinación de las pruebas",
            "Que Dios Majestuosoqueha observado": "Que Dios Majestuoso que ha observado",
            "Nuestro Senordemil cientoochentayseis": "Nuestro Señor de mil ciento ochenta y seis",
            "mi tio mediocomonovicioalmonasterio": "mi tio medio como novicio al monasterio",
            "lugardondehevivido desdeentonces": "lugar donde he vivido desde entonces",
            "siemprehasostenidofirmementequelosmonjesdebenquedarse": "siempre ha sostenido firmemente que los monjes deben quedarse",
            "Fulk me habló antes de que dejara alabad, ymedijo": "Fulk me habló antes de que dejara alabad, y me dijo",
            "vestidoconelsimplehabitoblanco": "vestido con el simple hábito blanco",
            "campamentohabian": "campamento habían",
            "Laspalabrasrompieroncualquierhechizoenelqueme": "Las palabras rompieron cualquier hechizo en el que me",
            "representarenvuestrosrelatos": "representar en vuestros relatos",
            "suspoderesmisticosnohacensino": "sus poderes místicos no hacen sino",
            "depersonas oanimalesmundanos": "de personas o animales mundanos",
            "Queelcontextoyelsentidocomunseantuguia": "Que el contexto y el sentido común sean tu guía",
        }
        for source, expected in samples.items():
            with self.subTest(source=source):
                self.assertIn(expected, sanitize_markdown(source))

        protected = "Bonisagus Bjornaer Quaesitores Intellego Ex Miscellanea Ars Magica Cistercienses Rievaulx Stonehenge Pythagoranis"
        self.assertEqual(sanitize_markdown(protected), protected)
        valid_long = "características responsabilidad administración experimentación"
        self.assertEqual(sanitize_markdown(valid_long), valid_long)
        noisy = "data:image/png;base64,ABC <!-- image --> ![Image](data:image/png;base64,ABC)"
        self.assertNotIn("data:image", sanitize_markdown(noisy))
        self.assertNotIn("<!-- image -->", sanitize_markdown(noisy))

        before = detect_suspicious_glued_tokens("Diariode Majestuosoqueha Bonisagus")
        repaired, metrics = repair_glued_words_v4("Diariode Majestuosoqueha Bonisagus")
        after = detect_suspicious_glued_tokens(repaired)
        self.assertGreater(before["suspicious_count"], after["suspicious_count"])
        self.assertGreaterEqual(metrics["exact_fixes"], 2)

    def legacy_pdf_to_word_docling_uses_placeholder(self):
        from fusion_reader_v2.pdf_to_docx import convert_pdf_to_docx, JobStatus
        from unittest import mock
        
        with mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=True), \
             mock.patch("subprocess.Popen") as mock_popen, \
             mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=["   "]), \
             mock.patch("subprocess.run"):
            
            mock_proc = mock.MagicMock()
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = ("ok", "")
            mock_popen.return_value = mock_proc
            
            # Temporary mock for find md files
            with mock.patch("pathlib.Path.glob", return_value=[Path("dummy.md")]):
                convert_pdf_to_docx("test.pdf", "test.docx")
            
            args, kwargs = mock_popen.call_args
            cmd = args[0]
            self.assertIn("--image-export-mode", cmd)
            self.assertIn("placeholder", cmd)

    def legacy_pdf_to_word_ocr_cleanup_logic(self):
        from fusion_reader_v2.pdf_to_docx import _clean_ocr_line, _is_noise_line, _detect_heading, _should_merge_with_previous
        
        # 1. Cleaning
        self.assertEqual(_clean_ocr_line("...  Texto sucio!!!"), "Texto sucio!!!")
        self.assertEqual(_clean_ocr_line("Palabra — con raya"), "Palabra - con raya")
        
        # 2. Noise
        self.assertTrue(_is_noise_line("A E A E A E"))
        self.assertTrue(_is_noise_line("... --- ..."))
        self.assertFalse(_is_noise_line("Este es un párrafo válido."))
        
        # 3. Headings
        self.assertEqual(_detect_heading("CAPÍTULO 1: EL COMIENZO"), "Heading1")
        self.assertEqual(_detect_heading("Introducción"), "Heading2")
        self.assertIsNone(_detect_heading("Esta es una línea normal que termina en punto."))
        
        # 4. Merging
        self.assertTrue(_should_merge_with_previous("Esta línea no termina en punto", "esta continúa"))
        self.assertFalse(_should_merge_with_previous("Esta sí termina en punto.", "Esta es nueva"))
        self.assertTrue(_should_merge_with_previous("Palabra cortada-", "continuación"))

    def legacy_mcp_memory_server_core_logic(self):
        from scripts import fusion_memory_mcp_server as mcp_mod
        
        # 1. list
        files = mcp_mod.allowed_memory_files()
        self.assertIn("project_state.md", files)
        self.assertIn("decisions.md", files)
        self.assertIn("boundaries.md", files)
        
        # 2. read project_state
        content = mcp_mod.read_memory_file("project_state.md")
        self.assertIn("Project State", content)
        self.assertIn("2b7024b", content)
        self.assertIn("164 OK", content)
        
        # 3. Security: read outside
        fail_content = mcp_mod.read_memory_file("../FUSION_READER_V2_STATE.md")
        self.assertTrue(fail_content.startswith("Error:"))
        
        # 4. Security: read .env
        fail_env = mcp_mod.read_memory_file(".env")
        self.assertTrue(fail_env.startswith("Error:"))
        
        # 5. Security: read non-md
        # (Actually the whitelist already prevents this, but test it)
        fail_bin = mcp_mod.read_memory_file("audio_cache/some.wav")
        self.assertTrue(fail_bin.startswith("Error:"))
        
        # 6. search
        results = mcp_mod.search_memory("cinco ejes")
        self.assertGreater(len(results), 0)
        found_files = [r["file"] for r in results]
        self.assertIn("project_state.md", found_files)
        # Check one of the fragments
        self.assertIn("cinco ejes", results[0]["content"].lower())
        
        # 7. specific helpers
        state_content = mcp_mod.read_memory_file("project_state.md")
        self.assertIn("Project State", state_content)
        
        boundaries_content = mcp_mod.read_memory_file("boundaries.md")
        self.assertIn("System Boundaries", boundaries_content)
        self.assertIn("Doctora Lucy", boundaries_content)
        
        steps_content = mcp_mod.read_memory_file("next_steps.md")
        self.assertIn("Next Steps", steps_content)


if __name__ == "__main__":
    unittest.main()

def load_tests(loader, tests, pattern):
    if pattern is not None:
        return unittest.TestSuite()

    suite = unittest.TestSuite()
    module_names = (
        "tests.test_audio_export",
        "tests.test_conversation",
        "tests.test_dialogue",
        "tests.test_external_research",
        "tests.test_notes_metrics",
        "tests.test_pdf_to_word",
        "tests.test_reader",
        "tests.test_server_api",
        "tests.test_tts",
    )
    for module_name in module_names:
        suite.addTests(loader.loadTestsFromName(module_name))
    return suite
