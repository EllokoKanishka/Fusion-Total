from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest
import wave

from fusion_reader_v2.audio_export import _concat_wav_with_wave
from fusion_reader_v2 import pdf_to_docx
from fusion_reader_v2.tts import AudioCache
from fusion_reader_v2.web.routes.audio import handle_audio_get
from tests.helpers import managed_test_app


class StatusEfficiencyTests(unittest.TestCase):
    def test_status_reuses_document_chunks_and_one_health_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            with managed_test_app(root=Path(folder)) as app:
                app.load_text("fixture", "Fixture", "Analysis " * 20000, prefetch=False)
                with (
                    patch("fusion_reader_v2.facade.Document.from_text", wraps=pdf_document_factory()) as build,
                    patch.object(app.tts, "health", wraps=app.tts.health) as health,
                ):
                    first = app.status()
                    second = app.status()
                self.assertEqual(build.call_count, 0)
                self.assertEqual(health.call_count, 2)
                self.assertEqual(first["main_document"], second["main_document"])

    def test_cache_summary_reuses_scan_but_manual_inspection_is_fresh(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = AudioCache(Path(folder))
            with patch.object(cache, "_cache_plan", wraps=cache._cache_plan) as scan:
                cache.summary()
                cache.summary()
                self.assertEqual(scan.call_count, 1)
                cache.inspect()
                self.assertEqual(scan.call_count, 2)
                cache.summary(max_age=0)
                self.assertEqual(scan.call_count, 3)

    def test_different_wav_lengths_concatenate_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for index, frames in enumerate((31, 101)):
                path = Path(folder) / f"{index}.wav"
                with wave.open(str(path), "wb") as stream:
                    stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    stream.writeframes(b"\0\0" * frames)
                paths.append(path)
            output = Path(folder) / "joined.wav"
            self.assertEqual(_concat_wav_with_wave(paths, output), "wave")
            with wave.open(str(output), "rb") as stream:
                self.assertEqual(stream.getnframes(), 132)

    def test_pdf_batch_preserves_blank_page_and_uses_one_extraction(self):
        with (
            patch.object(pdf_to_docx, "_page_count", return_value=3),
            patch.object(pdf_to_docx, "_pdftotext_page", return_value="First\f\fThird\f") as extract,
        ):
            pages = pdf_to_docx._extract_pages_text(Path("fixture.pdf"))
        self.assertEqual(pages, ["First", "", "Third"])
        self.assertEqual(extract.call_count, 1)

    def test_pdf_cancelled_before_extraction_never_spawns(self):
        with (
            patch.object(pdf_to_docx, "_page_count", return_value=3),
            patch.object(pdf_to_docx, "_pdftotext_page") as extract,
        ):
            with self.assertRaises(RuntimeError):
                pdf_to_docx._extract_pages_text(Path("fixture.pdf"), job=SimpleNamespace(cancelled=True))
        extract.assert_not_called()

    def test_canceling_export_is_still_a_consultable_resource(self):
        responder = SimpleNamespace(
            app=SimpleNamespace(audio_export_status=lambda _: {"ok": False, "state": "canceling"}), _json=Mock()
        )
        self.assertTrue(handle_audio_get(responder, "/api/audio-export/status/owned", "/api/audio-export/status/owned"))
        self.assertEqual(responder._json.call_args.args[0], 200)


def pdf_document_factory():
    from fusion_reader_v2.reader import Document

    return Document.from_text
