import unittest
import tempfile
import wave
from pathlib import Path
from unittest import mock
from fusion_reader_v2.audio_export import concat_wav_files, sanitize_audio_title
from tests.helpers import (
    test_app,
    managed_test_app,
    wait_for_audio_export,
    SyntheticWavTTSProvider,
    LengthLimitedSyntheticWavTTSProvider,
    FailingTTSProvider,
    manual_document,
    make_reading_document,
)

class AudioExportTests(unittest.TestCase):
    def test_audio_export_test_app_uses_temporary_export_root(self):
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            root = Path(app._test_root)
            self.assertEqual(app.audio_export_root.name, "Descargas")
            self.assertTrue(root.name.startswith("fusion_reader_v2_test_"))
            self.assertTrue(root.exists())
        self.assertFalse(root.exists())

    def _assert_single_final_export(self, mode: str, *, title: str, chunks: list[str], export_kwargs: dict | None = None, setup=None) -> None:
        export_kwargs = dict(export_kwargs or {})
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            root = Path(app._test_root)
            export_dir = Path(app.audio_export_root)
            app.session.load(manual_document("doc", title, chunks))
            if setup is not None:
                setup(app)
            before = {p.name for p in export_dir.glob("*.wav")}
            job = app.start_audio_export(mode, **export_kwargs)
            final = wait_for_audio_export(app, job["job_id"])
            after = {p.name for p in export_dir.glob("*.wav")}
            new_files = sorted(after - before)
            self.assertEqual(len(new_files), 1)
            self.assertEqual(Path(final["output_path"]).resolve(), (export_dir / new_files[0]).resolve())
            self.assertTrue((export_dir / new_files[0]).exists())
            self.assertFalse(list(export_dir.glob(".audio_export_*.txt")))
        self.assertFalse(root.exists())

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
        with managed_test_app(tts=SyntheticWavTTSProvider(delay_seconds=0.1)) as app:
            root = Path(app._test_root)
            app.load_text("doc", "Doc", make_reading_document("Doc", 10), prefetch=False)
            first = app.start_audio_export("full")
            second = app.start_audio_export("full")
            self.assertFalse(second["ok"])
            self.assertEqual(second["error"], "audio_export_busy")
            app.cancel_audio_export(first["job_id"])
            status = wait_for_audio_export(app, first["job_id"])
            self.assertEqual(status["state"], "cancelled")
            self.assertFalse(getattr(app, "_audio_export_thread").is_alive())
        self.assertFalse(root.exists())

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

    def test_audio_export_modes_create_exactly_one_final_wav(self):
        cases = (
            ("current", dict(title="Doc", chunks=["u", "d", "t"], setup=lambda app: app.jump(2))),
            ("block", dict(title="Doc", chunks=["u", "d", "t"], export_kwargs={"block": 3})),
            ("range", dict(title="Doc", chunks=["u", "d", "t", "c"], export_kwargs={"start": 2, "end": 4})),
            ("full", dict(title="Doc", chunks=["u", "d"], export_kwargs={})),
        )
        for mode, kwargs in cases:
            with self.subTest(mode=mode):
                self._assert_single_final_export(mode, **kwargs)

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

    def test_audio_export_download_root_boundaries(self):
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            root = Path(app._test_root)
            export_dir = Path(app.audio_export_root)
            app.session.load(manual_document("doc", "Doc", ["u"]))
            job = app.start_audio_export("full")
            status = wait_for_audio_export(app, job["job_id"])
            self.assertEqual(status["state"], "done")
            valid = app.get_audio_export_download(job["job_id"])
            self.assertTrue(valid["ok"])
            self.assertEqual(Path(valid["path"]).resolve(), Path(status["output_path"]).resolve())

            job_state = app._audio_export_jobs[job["job_id"]]
            outside = root.parent / "outside_audio_export.wav"
            symlink = export_dir / "outside_link.wav"
            missing = export_dir / "missing_inside_root.wav"
            outside.write_text("outside", encoding="utf-8")
            symlink.symlink_to(outside)
            try:
                job_state.output_path = str(outside)
                self.assertEqual(app.get_audio_export_download(job["job_id"])["error"], "audio_export_path_invalid")
                job_state.output_path = str(symlink)
                self.assertEqual(app.get_audio_export_download(job["job_id"])["error"], "audio_export_path_invalid")
                job_state.output_path = str(missing)
                self.assertEqual(app.get_audio_export_download(job["job_id"])["error"], "audio_export_file_missing")
            finally:
                symlink.unlink(missing_ok=True)
                outside.unlink(missing_ok=True)
        self.assertFalse(root.exists())

    def test_audio_export_sequential_exports_create_one_suffixed_sibling_only(self):
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            root = Path(app._test_root)
            export_dir = Path(app.audio_export_root)
            app.session.load(manual_document("doc", "Doc", ["uno"]))
            first = app.start_audio_export("full")
            first_status = wait_for_audio_export(app, first["job_id"])
            second = app.start_audio_export("full")
            second_status = wait_for_audio_export(app, second["job_id"])
            self.assertEqual(first_status["state"], "done")
            self.assertEqual(second_status["state"], "done")
            files = sorted(p.name for p in export_dir.glob("*.wav"))
            self.assertEqual(len(files), 2)
            self.assertIn(Path(first_status["output_path"]).name, files)
            self.assertIn(Path(second_status["output_path"]).name, files)
            self.assertTrue(any(name.endswith("_2.wav") for name in files))
            job_ids = {first["job_id"], second["job_id"]}
            self.assertEqual(set(app._audio_export_jobs), job_ids)
            for _ in range(3):
                app.audio_export_overview()
                app.audio_export_status(first["job_id"])
                app.audio_export_status(second["job_id"])
            self.assertEqual(set(app._audio_export_jobs), job_ids)
            self.assertFalse(getattr(app, "_audio_export_thread").is_alive())
        self.assertFalse(root.exists())

    def test_audio_export_runtime_default_uses_descargas(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            downloads = home / "Descargas"
            downloads.mkdir()
            with mock.patch("fusion_reader_v2.pdf_to_docx.Path.home", return_value=home):
                app = test_app(tts=SyntheticWavTTSProvider(), root=home / "state", audio_export_root=None)
            self.assertEqual(app.audio_export_root, downloads.resolve())

    def test_audio_export_cancel_cleanup_removes_partial_files_and_thread_stops(self):
        with managed_test_app(tts=SyntheticWavTTSProvider(delay_seconds=0.1)) as app:
            root = Path(app._test_root)
            export_dir = Path(app.audio_export_root)
            app.session.load(manual_document("doc", "Doc", ["u", "d", "t", "c"]))
            job = app.start_audio_export("full")
            app.cancel_audio_export(job["job_id"])
            status = wait_for_audio_export(app, job["job_id"])
            self.assertEqual(status["state"], "cancelled")
            self.assertFalse(list(export_dir.glob("*.wav")))
            self.assertFalse(list(export_dir.glob(".audio_export_*.txt")))
            self.assertFalse(getattr(app, "_audio_export_thread").is_alive())
        self.assertFalse(root.exists())

    def test_audio_export_error_cleanup_removes_partial_files_and_thread_stops(self):
        with managed_test_app(tts=FailingTTSProvider()) as app:
            root = Path(app._test_root)
            export_dir = Path(app.audio_export_root)
            app.session.load(manual_document("doc", "Doc", ["u", "d", "t"]))
            job = app.start_audio_export("full")
            status = wait_for_audio_export(app, job["job_id"])
            self.assertEqual(status["state"], "error")
            self.assertFalse(list(export_dir.glob("*.wav")))
            self.assertFalse(list(export_dir.glob(".audio_export_*.txt")))
            self.assertFalse(getattr(app, "_audio_export_thread").is_alive())
        self.assertFalse(root.exists())

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

    def test_concat_wav_files_removes_temporary_concat_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.wav"
            second = root / "second.wav"
            with wave.open(str(first), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(b"\0\0" * 1600)
            with wave.open(str(second), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(22050)
                f.writeframes(b"\0\0" * 1600)
            out = root / "ffmpeg_out.wav"
            created_list_files: list[Path] = []

            def fake_run(cmd, check, stdout, stderr, text):
                list_path = Path(cmd[cmd.index("-i") + 1])
                created_list_files.append(list_path)
                out.touch()
                return mock.Mock()

            with mock.patch("fusion_reader_v2.audio_export.shutil.which", return_value="/usr/bin/ffmpeg"), \
                 mock.patch("fusion_reader_v2.audio_export.subprocess.run", side_effect=fake_run):
                method = concat_wav_files([first, second], out)
            self.assertEqual(method, "ffmpeg")
            self.assertTrue(out.exists())
            self.assertFalse(any(path.exists() for path in created_list_files))

    def test_audio_export_filename_sanitizer_blocks_path_traversal(self):
        self.assertEqual(sanitize_audio_title("../danger"), "danger")
