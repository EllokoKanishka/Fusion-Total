from __future__ import annotations

import tempfile
import time
import unittest
import wave
from pathlib import Path

from fusion_reader_v2.config import create_settings
from fusion_reader_v2.web.context import WebContext
from tests.helpers import NullSTTProvider, SyntheticWavTTSProvider, managed_test_app


def write_wav(path: Path, seconds: float = 0.1) -> None:
    frames = max(1, int(16000 * seconds))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * frames)


def wait_for_media(context: WebContext, job_id: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = context.media.status(job_id)
        if latest.get("terminal"):
            return latest
        time.sleep(0.02)
    raise AssertionError(f"media job did not finish: {latest}")


class MediaProcessingTests(unittest.TestCase):
    def context(self, root: Path, app) -> WebContext:
        settings = create_settings(
            repository_root=Path.cwd(),
            environ={
                "HOME": str(root / "home"),
                "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
            },
        )
        return WebContext(app=app, settings=settings, runtime_info={})

    def test_transcription_creates_pdf_and_mounts_as_normal_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(
                root=root / "app",
                stt=NullSTTProvider("La conferencia trata sobre técnica y sociedad."),
            ) as app:
                context = self.context(root, app)
                source = root / "conferencia.wav"
                write_wav(source)
                started = context.media.start(
                    operation="transcribe",
                    filename=source.name,
                    mime="audio/wav",
                    input_path=source,
                    voice=app.voice.voice,
                )
                status = wait_for_media(context, str(started["job_id"]))
                self.assertEqual(status["state"], "done", status)
                self.assertTrue(Path(status["output"]["pdf"]["filename"]).suffix == ".pdf")
                artifact = context.media.artifact(str(started["job_id"]), "pdf")
                self.assertGreater(Path(str(artifact["path"])).stat().st_size, 100)

                mounted = context.media.mount(str(started["job_id"]))
                self.assertTrue(mounted["mounted"])
                self.assertEqual(app.status()["document"]["source_type"], "media_transcript")
                self.assertIn("técnica y sociedad", app.session.document.text)
                self.assertTrue(Path(app._main_source_path).exists())
                self.assertTrue(context.shutdown_jobs()["ok"])
            with managed_test_app(root=root / "app") as restored:
                self.assertEqual(restored.status()["document"]["source_type"], "media_transcript")
                self.assertIn("técnica y sociedad", restored.session.document.text)

    def test_translation_creates_spanish_pdf_and_audio_with_selected_voice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tts = SyntheticWavTTSProvider()
            with managed_test_app(
                root=root / "app",
                tts=tts,
                stt=NullSTTProvider("Technology changes institutions."),
            ) as app:
                context = self.context(root, app)
                source = root / "lecture.mp4"
                write_wav(source)
                started = context.media.start(
                    operation="translate",
                    filename=source.name,
                    mime="video/mp4",
                    input_path=source,
                    voice="female_03.wav",
                )
                status = wait_for_media(context, str(started["job_id"]))
                self.assertEqual(status["state"], "done", status)
                self.assertIn("translated_pdf", status["output"])
                self.assertIn("audio", status["output"])
                self.assertTrue(context.media.artifact(str(started["job_id"]), "audio")["ok"])
                self.assertTrue(tts.calls)
                self.assertTrue(all(call[1:] == ("female_03.wav", "es") for call in tts.calls))
                mounted = context.media.mount(str(started["job_id"]))
                self.assertTrue(mounted["mounted"])
                self.assertEqual(app.status()["document"]["source_type"], "media_translation")
                self.assertIn("Entendido", app.session.document.text)
                self.assertTrue(context.shutdown_jobs()["ok"])

    def test_media_without_audio_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(root=root / "app") as app:
                context = self.context(root, app)
                source = root / "not-media.txt"
                source.write_text("not media", encoding="utf-8")
                started = context.media.start(
                    operation="transcribe",
                    filename=source.name,
                    mime="text/plain",
                    input_path=source,
                    voice=app.voice.voice,
                )
                status = wait_for_media(context, str(started["job_id"]))
                self.assertEqual(status["state"], "error")
                self.assertIn(status["error"], {"media_unreadable", "media_probe_invalid"})
                self.assertFalse(source.exists())
                self.assertTrue(context.shutdown_jobs()["ok"])


if __name__ == "__main__":
    unittest.main()
