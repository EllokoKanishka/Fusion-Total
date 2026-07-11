import unittest
import tempfile
import wave
from pathlib import Path
from unittest import mock
from fusion_reader_v2.audio_export import concat_wav_files, sanitize_audio_title
from tests.helpers import (
    test_app,
    wait_for_audio_export,
    SyntheticWavTTSProvider,
    LengthLimitedSyntheticWavTTSProvider,
    manual_document,
    make_reading_document,
)

class AudioExportTests(unittest.TestCase):
    def test_audio_export_test_app_uses_temporary_export_root(self):
        app = test_app(tts=SyntheticWavTTSProvider())
        self.assertEqual(app.audio_export_root.name, "Descargas")
        self.assertTrue(app.audio_export_root.parent.name.startswith("fusion_reader_v2_test_"))

    def test_audio_export_generates_files_and_reports_progress(self):
        app = test_app(tts=SyntheticWavTTSProvider())
        app.load_text("doc", "Doc", make_reading_document("Doc", 5), prefetch=False)
        out = app.start_audio_export("full")
        self.assertTrue(out["ok"])
        final = wait_for_audio_export(app, out["job_id"])
        self.assertEqual(final["state"], "done")
        self.assertTrue(Path(final["output_path"]).exists())

    def test_audio_export_cancels_running_job(self):
        app = test_app(tts=SyntheticWavTTSProvider(delay_seconds=0.05))
        app.load_text("doc", "Doc", make_reading_document("Doc", 10), prefetch=False)
        out = app.start_audio_export("full")
        app.cancel_audio_export(out["job_id"])
        status = wait_for_audio_export(app, out["job_id"])
        self.assertEqual(status["state"], "cancelled")

    def test_audio_export_limits_concurrent_jobs(self):
        app = test_app()
        app.load_text("doc", "Doc", "U.", prefetch=False)
        app.start_audio_export("full")
        second = app.start_audio_export("full")
        self.assertFalse(second["ok"])

    def test_audio_export_current_block_uses_current_cursor(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["u", "d", "t"]))
        app.jump(2)
        job = app.start_audio_export("current")
        wait_for_audio_export(app, job["job_id"])
        self.assertEqual(provider.calls[0][0], "d")

    def test_audio_export_specific_block(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["u", "d", "t"]))
        job = app.start_audio_export("block", block=3)
        wait_for_audio_export(app, job["job_id"])
        self.assertEqual(provider.calls[0][0], "t")

    def test_audio_export_range(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["u", "d", "t", "c"]))
        job = app.start_audio_export("range", start=2, end=4)
        wait_for_audio_export(app, job["job_id"])
        self.assertEqual([c[0] for c in provider.calls], ["d", "t", "c"])

    def test_audio_export_full_document(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["u", "d"]))
        job = app.start_audio_export("full")
        wait_for_audio_export(app, job["job_id"])
        self.assertEqual([c[0] for c in provider.calls], ["u", "d"])

    def test_audio_export_rejects_invalid_ranges_and_missing_document(self):
        app = test_app()
        self.assertEqual(app.start_audio_export("current")["error"], "no_document_loaded")
        app.session.load(manual_document("doc", "Doc", ["u"]))
        self.assertEqual(app.start_audio_export("block", block=5)["error"], "audio_export_block_out_of_range")

    def test_audio_export_uses_snapshot_even_if_session_changes(self):
        provider = SyntheticWavTTSProvider(delay_seconds=0.05)
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Orig", ["u", "d"]))
        job = app.start_audio_export("full")
        app.session.load(manual_document("new", "New", ["x"]))
        wait_for_audio_export(app, job["job_id"])
        self.assertEqual([c[0] for c in provider.calls], ["u", "d"])

    def test_audio_export_reuses_cache_without_calling_tts_again(self):
        provider = SyntheticWavTTSProvider()
        app = test_app(tts=provider)
        app.session.load(manual_document("doc", "Doc", ["u"]))
        first = app.start_audio_export("full")
        wait_for_audio_export(app, first["job_id"])
        before = len(provider.calls)
        second = app.start_audio_export("full")
        wait_for_audio_export(app, second["job_id"])
        self.assertEqual(len(provider.calls), before)

    def test_audio_export_cancel_sets_cancelled_state(self):
        app = test_app(tts=SyntheticWavTTSProvider(delay_seconds=0.1))
        app.session.load(manual_document("doc", "Doc", ["u", "d"]))
        job = app.start_audio_export("full")
        app.cancel_audio_export(job["job_id"])
        status = wait_for_audio_export(app, job["job_id"])
        self.assertEqual(status["state"], "cancelled")

    def test_audio_export_download_stays_in_descargas(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            external_downloads = base / "Descargas reales simuladas"
            external_downloads.mkdir()
            sentinel = external_downloads / "sentinel.keep"
            sentinel.write_text("keep", encoding="utf-8")
            sandbox = base / "sandbox"
            app = test_app(tts=SyntheticWavTTSProvider(), root=sandbox)
            app.session.load(manual_document("doc", "Doc", ["u"]))
            job = app.start_audio_export("full")
            final = wait_for_audio_export(app, job["job_id"])
            target = Path(final["output_path"]).resolve()
            self.assertEqual(target.parent, (sandbox / "Descargas").resolve())
            self.assertEqual(list(external_downloads.iterdir()), [sentinel])
            self.assertEqual(sorted(p.name for p in (sandbox / "Descargas").glob("*.wav")), [target.name])

    def test_audio_export_runtime_default_uses_descargas(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            downloads = home / "Descargas"
            downloads.mkdir()
            with mock.patch("fusion_reader_v2.pdf_to_docx.Path.home", return_value=home):
                app = test_app(tts=SyntheticWavTTSProvider(), root=home / "state", audio_export_root=None)
            self.assertEqual(app.audio_export_root, downloads.resolve())

    def test_audio_export_does_not_break_read_current(self):
        app = test_app(tts=SyntheticWavTTSProvider())
        app.session.load(manual_document("doc", "Doc", ["u"]))
        job = app.start_audio_export("full")
        wait_for_audio_export(app, job["job_id"])
        self.assertTrue(app.read_current(play=False)["ok"])

    def test_audio_export_and_read_current_split_long_tts_requests_when_provider_rejects_big_input(self):
        provider = LengthLimitedSyntheticWavTTSProvider(max_chars=90)
        app = test_app(tts=provider)
        app.tts_segment_chars = 90
        # Text must be > 90 and > 80 (the internal max(80, limit) floor)
        text = "Esta es una frase suficientemente larga para superar el limite de noventa caracteres y forzar la segmentacion automatica del motor de tts."
        app.session.load(manual_document("doc", "Doc", [text]))
        job = app.start_audio_export("full")
        wait_for_audio_export(app, job["job_id"])
        # Should call synthesize multiple times (one for the 100+ char text which fails, then for segments)
        # Actually _synthesize_cached_with_settings calls it once, gets 400, then calls _synthesize_segmented_with_settings
        self.assertGreater(len(provider.calls), 1)

    def test_concat_wav_files_creates_valid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = []
            for i in range(2):
                p = root / f"{i}.wav"
                with wave.open(str(p), "wb") as f:
                    f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b"\0"*1600)
                inputs.append(p)
            out = root / "out.wav"
            concat_wav_files(inputs, out)
            self.assertTrue(out.exists())

    def test_audio_export_filename_sanitizer_blocks_path_traversal(self):
        self.assertEqual(sanitize_audio_title("../danger"), "danger")
