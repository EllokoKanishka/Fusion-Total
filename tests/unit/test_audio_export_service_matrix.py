from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import AudioArtifact
from fusion_reader_v2.audio_export import AudioExportJob, AudioExportSnapshot
from tests.helpers import FailingTTSProvider, SyntheticWavTTSProvider, managed_test_app


class MissingArtifactTTS(SyntheticWavTTSProvider):
    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        return AudioArtifact(True, path=Path("/missing/fusion.wav"), provider=self.name)


class UnavailableTTS(FailingTTSProvider):
    def health(self) -> dict:
        return {"ok": False, "detail": "offline"}


class AudioExportServiceMatrixTests(unittest.TestCase):
    def test_snapshot_start_shutdown_and_registry_boundaries(self) -> None:
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            service = app._audio_export_service
            self.assertEqual(service.start("current")["error"], "no_document_loaded")
            app.load_text("doc", "Doc", "Uno.\n\nDos.", prefetch=False)
            self.assertEqual(service.start("invalid")["error"], "audio_export_mode_invalid")
            self.assertEqual(service.start("block", block=99)["error"], "audio_export_block_out_of_range")
            self.assertEqual(service.start("range", start=2, end=1)["error"], "audio_export_range_invalid")

            with mock.patch.object(service.registry, "add", side_effect=RuntimeError("full")):
                self.assertEqual(service.start("current")["error"], "audio_export_registry_full")

            with mock.patch.object(app, "_background_work_is_open_locked", side_effect=[True, False]):
                self.assertEqual(service.start("current")["error"], "service_shutting_down")

        with managed_test_app(tts=UnavailableTTS()) as app:
            app.load_text("doc", "Doc", "Sin cache", prefetch=False)
            self.assertEqual(app._audio_export_service.start("current")["error"], "tts_unavailable_for_audio_export")

    def test_status_cancel_finish_and_download_rejections(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            managed_test_app(tts=SyntheticWavTTSProvider(), root=Path(tmp) / "app") as app,
        ):
            service = app._audio_export_service
            self.assertEqual(service.status("")["state"], "idle")
            self.assertEqual(service.status("missing")["error"], "audio_export_job_not_found")
            self.assertEqual(service.cancel("missing")["error"], "audio_export_job_not_found")
            self.assertEqual(service.download("missing")["error"], "audio_export_job_not_found")
            service.finish("missing", "done", "ignored")

            job = AudioExportJob("job", state="done", started_at=1.0, finished_at=2.0)
            service.registry.add(job.job_id, job)
            self.assertEqual(service.cancel(job.job_id)["state"], "done")
            self.assertEqual(service.download(job.job_id)["error"], "audio_export_not_ready")

            outside = Path(tmp) / "outside.wav"
            outside.write_bytes(b"RIFF")
            job.output_path = str(outside)
            job.filename = outside.name
            self.assertEqual(service.download(job.job_id)["error"], "audio_export_path_invalid")

            inside = app.audio_export_root / "missing.wav"
            job.output_path = str(inside)
            self.assertEqual(service.download(job.job_id)["error"], "audio_export_file_missing")

            target = app.audio_export_root / "target.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"RIFF")
            link = app.audio_export_root / "link.wav"
            link.symlink_to(target)
            job.output_path = str(link)
            self.assertEqual(service.download(job.job_id)["error"], "audio_export_path_invalid")

    def test_worker_handles_missing_job_snapshot_inputs_and_artifact(self) -> None:
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            service = app._audio_export_service
            service.worker("missing")
            no_snapshot = AudioExportJob("no-snapshot")
            service.registry.add(no_snapshot.job_id, no_snapshot)
            service.worker(no_snapshot.job_id)

            empty = AudioExportJob(
                "empty",
                filename="empty.wav",
                total_blocks=0,
                snapshot=AudioExportSnapshot("doc", "Doc", "voice", "es", 0, []),
            )
            service.registry.add(empty.job_id, empty)
            service.worker(empty.job_id)
            self.assertEqual(empty.error, "audio_export_no_inputs")

        with managed_test_app(tts=MissingArtifactTTS()) as app:
            snapshot = AudioExportSnapshot("doc", "Doc", app.voice.voice, app.voice.language, 1, [(1, "Texto")])
            job = AudioExportJob("artifact", filename="artifact.wav", total_blocks=1, snapshot=snapshot)
            app._audio_export_service.registry.add(job.job_id, job)
            app._audio_export_service.worker(job.job_id)
            self.assertEqual(job.error, "audio_export_missing_artifact")


if __name__ == "__main__":
    unittest.main()
