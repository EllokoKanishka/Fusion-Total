from __future__ import annotations

import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from fusion_reader_v2.config import create_settings
from fusion_reader_v2.web.context import WebContext
from tests.helpers import (
    BlockingSyntheticWavTTSProvider,
    FailingTTSProvider,
    NullSTTProvider,
    SyntheticWavTTSProvider,
    managed_test_app,
)


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
                artifact_path = Path(str(artifact["path"]))
                self.assertGreater(artifact_path.stat().st_size, 100)
                self.assertEqual(artifact_path.parent, context.media_artifacts_root)
                self.assertNotEqual(artifact_path.parent, context.settings.paths.downloads)
                downloads = context.settings.paths.downloads
                self.assertFalse(downloads.exists() and any(downloads.iterdir()))

                restarted_context = self.context(root, app)
                restored = restarted_context.media.status(str(started["job_id"]))
                self.assertEqual(restored["state"], "done")
                self.assertEqual(restarted_context.media.overview()["job_id"], started["job_id"])
                first_download = restarted_context.media.artifact(str(started["job_id"]), "pdf")
                second_download = restarted_context.media.artifact(str(started["job_id"]), "pdf")
                self.assertEqual(first_download["path"], second_download["path"])
                self.assertTrue(Path(str(second_download["path"])).is_file())

                mounted = restarted_context.media.mount(str(started["job_id"]))
                self.assertTrue(mounted["mounted"])
                self.assertEqual(app.status()["document"]["source_type"], "media_transcript")
                self.assertIn("técnica y sociedad", app.session.document.text)
                self.assertNotIn("Idioma detectado:", app.session.document.text)
                self.assertNotIn("— Transcripción", app.session.document.text)
                self.assertTrue(Path(app._main_source_path).exists())
                self.assertTrue(restarted_context.shutdown_jobs()["ok"])
                self.assertTrue(context.shutdown_jobs()["ok"])
            with managed_test_app(root=root / "app") as restored:
                self.assertEqual(restored.status()["document"]["source_type"], "media_transcript")
                self.assertIn("técnica y sociedad", restored.session.document.text)

    def test_preflight_rejects_oversized_input_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(root=root / "app") as app:
                context = self.context(root, app)
                status = context.media.capabilities(
                    operation="transcribe", input_bytes=context.media.max_input_bytes + 1
                )
                self.assertFalse(status["ok"])
                self.assertIn("media_too_large", status["errors"])
                self.assertEqual(status["max_input_bytes"], context.settings.limits.media_max_bytes)

    def test_preflight_reports_every_required_local_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(root=root / "app") as app:
                context = self.context(root, app)
                self.assertEqual(context.media.capabilities(operation="unknown")["error"], "media_operation_invalid")
                context.media.tts_health = lambda: {"ok": False, "detail": "down"}
                disk = type("DiskUsage", (), {"free": 1})()
                with (
                    mock.patch("fusion_reader_v2.services.media.shutil.which", return_value=None),
                    mock.patch("fusion_reader_v2.services.media.shutil.disk_usage", return_value=disk),
                    mock.patch.object(context.media.stt, "health", return_value={"ok": False}),
                    mock.patch.object(context.media.chat, "health", return_value={"ok": False}),
                ):
                    status = context.media.capabilities(
                        operation="translate", include_translated_pdf=True, include_spanish_audio=True
                    )
                self.assertFalse(status["ok"])
                self.assertEqual(
                    set(status["errors"]),
                    {
                        "ffprobe_not_available",
                        "ffmpeg_not_available",
                        "stt_not_available",
                        "translation_not_available",
                        "tts_not_available",
                        "media_disk_space_low",
                    },
                )

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
                self.assertNotIn("Idioma detectado:", app.session.document.text)
                self.assertNotIn("— Traducción al castellano", app.session.document.text)
                self.assertTrue(context.shutdown_jobs()["ok"])

    def test_selectable_outputs_skip_unrequested_artifacts_and_completed_job_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tts = SyntheticWavTTSProvider()
            with managed_test_app(
                root=root / "app",
                tts=tts,
                stt=NullSTTProvider("Technology changes institutions."),
            ) as app:
                context = self.context(root, app)
                source = root / "selective.wav"
                write_wav(source)
                started = context.media.start(
                    operation="translate",
                    filename=source.name,
                    mime="audio/wav",
                    input_path=source,
                    voice="female_03.wav",
                    include_original_pdf=False,
                    include_translated_pdf=True,
                    include_spanish_audio=False,
                )
                job_id = str(started["job_id"])
                status = wait_for_media(context, job_id)
                self.assertEqual(status["state"], "done", status)
                self.assertEqual(set(status["output"]), {"translated_pdf"})
                self.assertFalse(tts.calls)

                translated = context.media.artifact(job_id, "translated-pdf")
                translated_path = Path(str(translated["path"]))
                self.assertTrue(translated_path.is_file())
                mounted = context.media.mount(job_id)
                self.assertTrue(mounted["mounted"])
                self.assertEqual(app.status()["document"]["source_type"], "media_translation")

                closed = context.media.cancel(job_id)
                self.assertEqual(closed["state"], "idle")
                self.assertEqual(context.media.overview()["state"], "idle")
                self.assertTrue(translated_path.is_file())
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

    def test_failed_job_can_be_dismissed_and_legacy_manifest_is_not_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(root=root / "app") as app:
                context = self.context(root, app)
                source = root / "broken-media.txt"
                source.write_text("not media", encoding="utf-8")
                started = context.media.start(
                    operation="translate",
                    filename=source.name,
                    mime="text/plain",
                    input_path=source,
                    voice=app.voice.voice,
                )
                job_id = str(started["job_id"])
                status = wait_for_media(context, job_id)
                self.assertEqual(status["state"], "error")

                manifest = context.media.manifest_root / f"{job_id}.json"
                self.assertFalse(manifest.exists())

                # Simulate a stale failure manifest written by an older release.
                context.media._persist_job(context.media.jobs[job_id])
                self.assertTrue(manifest.exists())
                restarted_context = self.context(root, app)
                self.assertEqual(restarted_context.media.overview()["state"], "idle")
                self.assertFalse(manifest.exists())

                dismissed = context.media.cancel(job_id)
                self.assertEqual(dismissed["state"], "idle")
                self.assertEqual(context.media.overview()["state"], "idle")
                self.assertNotIn(job_id, context.media.jobs)
                self.assertTrue(restarted_context.shutdown_jobs()["ok"])
                self.assertTrue(context.shutdown_jobs()["ok"])

    def test_cancel_during_synthesis_removes_every_published_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tts = BlockingSyntheticWavTTSProvider()
            with managed_test_app(root=root / "app", tts=tts, stt=NullSTTProvider("An English lecture.")) as app:
                context = self.context(root, app)
                source = root / "cancel.wav"
                write_wav(source)
                started = context.media.start(
                    operation="translate",
                    filename=source.name,
                    mime="audio/wav",
                    input_path=source,
                    voice=app.voice.voice,
                    include_original_pdf=True,
                    include_translated_pdf=True,
                    include_spanish_audio=True,
                )
                job_id = str(started["job_id"])
                self.assertTrue(tts.started.wait(5), context.media.status(job_id))
                self.assertIn(
                    "media_processing_busy",
                    context.media.capabilities(operation="transcribe")["errors"],
                )
                canceling = context.media.cancel(job_id)
                self.assertEqual(canceling["state"], "canceling")
                tts.release.set()
                status = wait_for_media(context, job_id)
                self.assertEqual(status["state"], "cancelled", status)
                self.assertEqual(status["output"], {})
                self.assertFalse(context.media.artifact(job_id, "pdf")["ok"])
                self.assertFalse(context.media.artifact(job_id, "translated-pdf")["ok"])

    def test_late_tts_failure_keeps_mountable_partial_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(
                root=root / "app", tts=FailingTTSProvider(), stt=NullSTTProvider("An English lecture.")
            ) as app:
                context = self.context(root, app)
                source = root / "partial.wav"
                write_wav(source)
                started = context.media.start(
                    operation="translate",
                    filename=source.name,
                    mime="audio/wav",
                    input_path=source,
                    voice=app.voice.voice,
                    include_original_pdf=True,
                    include_translated_pdf=True,
                    include_spanish_audio=True,
                )
                job_id = str(started["job_id"])
                status = wait_for_media(context, job_id)
                self.assertEqual(status["state"], "partial", status)
                self.assertEqual(status["error"], "tts_down")
                self.assertEqual(set(status["output"]), {"pdf", "translated_pdf"})
                self.assertTrue(context.media.mount(job_id)["mounted"])

    def test_expired_manifest_is_deleted_and_cannot_resurrect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(root=root / "app", stt=NullSTTProvider("Clase.")) as app:
                context = self.context(root, app)
                source = root / "old.wav"
                write_wav(source)
                started = context.media.start(
                    operation="transcribe",
                    filename=source.name,
                    mime="audio/wav",
                    input_path=source,
                    voice=app.voice.voice,
                )
                job_id = str(started["job_id"])
                self.assertEqual(wait_for_media(context, job_id)["state"], "done")
                manifest = context.media.manifest_root / f"{job_id}.json"
                self.assertTrue(manifest.exists())
                context.media.jobs[job_id].updated_at = time.time() - 10
                context.media.ttl_seconds = 1
                context.media.registry.ttl_seconds = 1
                context.media._persist_job(context.media.jobs[job_id])
                self.assertEqual(context.media.overview()["state"], "idle")
                self.assertNotIn(job_id, context.media.jobs)
                self.assertFalse(manifest.exists())

    def test_duration_limit_fails_before_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stt = NullSTTProvider("No debería ejecutarse.")
            with managed_test_app(root=root / "app", stt=stt) as app:
                context = self.context(root, app)
                context.media.max_duration_seconds = 0.01
                source = root / "long.wav"
                write_wav(source, seconds=0.1)
                started = context.media.start(
                    operation="transcribe",
                    filename=source.name,
                    mime="audio/wav",
                    input_path=source,
                    voice=app.voice.voice,
                )
                status = wait_for_media(context, str(started["job_id"]))
                self.assertEqual(status["state"], "error")
                self.assertEqual(status["error"], "media_duration_exceeded")
                self.assertFalse(stt.calls)


if __name__ == "__main__":
    unittest.main()
