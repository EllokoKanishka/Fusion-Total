import unittest
import tempfile
import wave
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from fusion_reader_v2.audio_export import concat_wav_files, sanitize_audio_title
from tests.helpers import (
    test_app,
    managed_test_app,
    close_test_app,
    wait_for_audio_export,
    SyntheticWavTTSProvider,
    BlockingSyntheticWavTTSProvider,
    LengthLimitedSyntheticWavTTSProvider,
    FailingTTSProvider,
    manual_document,
    make_reading_document,
)


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class TrackingLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: int | None = None

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if timeout == -1:
            acquired = self._lock.acquire(blocking)
        else:
            acquired = self._lock.acquire(blocking, timeout)
        if acquired:
            self._owner = threading.get_ident()
        return acquired

    def release(self) -> None:
        self._owner = None
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def held_by_current_thread(self) -> bool:
        return self._owner == threading.get_ident()


@contextmanager
def guard_background_work_queries(app, *, query_seen: threading.Event | None = None):
    original = app._background_work_is_open

    def guarded():
        if query_seen is not None:
            query_seen.set()
        if getattr(app._tts_gate, "_is_owned", lambda: False)():
            raise AssertionError("_background_work_is_open() called while holding _tts_gate")
        held_by_current_thread = getattr(app._prefetch_lock, "held_by_current_thread", None)
        if callable(held_by_current_thread) and held_by_current_thread():
            raise AssertionError("_background_work_is_open() called while holding _prefetch_lock")
        return original()

    app._background_work_is_open = guarded
    try:
        yield
    finally:
        app._background_work_is_open = original


class AudioExportTests(unittest.TestCase):
    def test_audio_export_test_app_uses_temporary_export_root(self):
        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            root = Path(app._test_root)
            self.assertEqual(app.audio_export_root.name, "Descargas")
            self.assertTrue(root.name.startswith("fusion_reader_v2_test_"))
            self.assertTrue(root.exists())
        self.assertFalse(root.exists())

    def _assert_single_final_export(
        self, mode: str, *, title: str, chunks: list[str], export_kwargs: dict | None = None, setup=None
    ) -> None:
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

    def test_close_test_app_waits_for_running_prefetch_before_tempdir_cleanup(self):
        provider = BlockingSyntheticWavTTSProvider()
        cleanup_started = threading.Event()
        cleanup_finished = threading.Event()
        cleanup_errors: list[Exception] = []

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.prefetch_ahead = 1
            app.load_text("doc", "Doc", make_reading_document("Doc", 24), prefetch=True)
            self.assertTrue(provider.started.wait(5))
            self.assertTrue(len(app._prefetch_futures) >= 2)

            def run_cleanup() -> None:
                cleanup_started.set()
                try:
                    close_test_app(app)
                except Exception as exc:  # pragma: no cover - surfaced by assertions below
                    cleanup_errors.append(exc)
                finally:
                    cleanup_finished.set()

            cleanup_thread = threading.Thread(target=run_cleanup, name="cleanup-thread")
            cleanup_thread.start()
            try:
                self.assertTrue(cleanup_started.wait(5))
                self.assertTrue(root.exists())
                self.assertFalse(cleanup_finished.wait(0.1))
                provider.release.set()
                cleanup_thread.join(5)
                self.assertFalse(cleanup_thread.is_alive())
                self.assertTrue(cleanup_finished.is_set())
                self.assertFalse(cleanup_errors, cleanup_errors)
                time.sleep(0.05)
                self.assertFalse(root.exists())
                self.assertFalse(any(thread.is_alive() for thread in getattr(app._executor, "_threads", ())))
                self.assertFalse(app._prefetch_futures)
            finally:
                provider.release.set()
                cleanup_thread.join(5)

    def test_close_test_app_cancels_queued_prefetch_future_before_it_starts(self):
        provider = BlockingSyntheticWavTTSProvider()
        cleanup_started = threading.Event()
        cleanup_finished = threading.Event()
        cleanup_errors: list[Exception] = []

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.prefetch_ahead = 1
            app.load_text("doc", "Doc", make_reading_document("Doc", 24), prefetch=True)
            self.assertTrue(provider.started.wait(5))
            futures = list(app._prefetch_futures.values())
            self.assertEqual(len(futures), 2)
            queued_future = futures[1]

            def run_cleanup() -> None:
                cleanup_started.set()
                try:
                    close_test_app(app)
                except Exception as exc:  # pragma: no cover - surfaced by assertions below
                    cleanup_errors.append(exc)
                finally:
                    cleanup_finished.set()

            cleanup_thread = threading.Thread(target=run_cleanup, name="cleanup-thread")
            cleanup_thread.start()
            try:
                self.assertTrue(cleanup_started.wait(5))
                self.assertTrue(root.exists())
                self.assertFalse(cleanup_finished.wait(0.1))
                provider.release.set()
                cleanup_thread.join(5)
                self.assertFalse(cleanup_thread.is_alive())
                self.assertTrue(cleanup_finished.is_set())
                self.assertFalse(cleanup_errors, cleanup_errors)
                time.sleep(0.05)
                self.assertFalse(root.exists())
                self.assertTrue(queued_future.cancelled())
                self.assertEqual(len(provider.calls), 1)
                self.assertFalse(app._prefetch_futures)
            finally:
                provider.release.set()
                cleanup_thread.join(5)

    def test_test_app_registers_cleanup_via_unittest_case(self):
        observed: dict[str, object] = {}

        class CleanupProbe(unittest.TestCase):
            def runTest(inner_self):
                app = test_app(tts=SyntheticWavTTSProvider())
                observed["root"] = Path(app._test_root)
                observed["registered"] = bool(getattr(app, "_test_cleanup_registered", False))
                inner_self.assertTrue(observed["registered"])
                inner_self.assertTrue(observed["root"].exists())

        result = unittest.TestResult()
        CleanupProbe().run(result)
        self.assertTrue(result.wasSuccessful())
        self.assertTrue(observed["registered"])
        self.assertFalse(observed["root"].exists())

    def test_shutdown_rejects_audio_export_after_closing_begins(self):
        provider = BlockingSyntheticWavTTSProvider()
        export_hook_entered = threading.Event()
        release_export_hook = threading.Event()
        read_result: dict[str, object] = {}
        export_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}

        def run_read(app) -> None:
            try:
                read_result["out"] = app.read_current(play=False)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                read_result["exc"] = exc

        def run_export(app) -> None:
            try:
                export_result["out"] = app.start_audio_export("full")
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                export_result["exc"] = exc

        def run_shutdown(app) -> None:
            try:
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.load_text("doc", "Doc", make_reading_document("Doc", 4), prefetch=False)

            def block_export_registration() -> None:
                export_hook_entered.set()
                release_export_hook.wait()

            with mock.patch.object(app, "_before_audio_export_registration", side_effect=block_export_registration):
                read_thread = threading.Thread(target=run_read, args=(app,), name="read-thread")
                read_thread.start()
                self.assertTrue(provider.started.wait(5))

                export_thread = threading.Thread(target=run_export, args=(app,), name="export-thread")
                export_thread.start()
                self.assertTrue(export_hook_entered.wait(5))

                shutdown_thread = threading.Thread(target=run_shutdown, args=(app,), name="shutdown-thread")
                shutdown_thread.start()

                self.assertTrue(wait_until(lambda: app._background_work_state == "closing", timeout=5))
                release_export_hook.set()

                export_thread.join(5)
                self.assertFalse(export_thread.is_alive())
                self.assertNotIn("exc", export_result, export_result.get("exc"))
                self.assertEqual(export_result["out"]["error"], "service_shutting_down")
                self.assertFalse(app._audio_export_jobs)
                self.assertIsNone(app._audio_export_thread)

                provider.release.set()
                read_thread.join(5)
                shutdown_thread.join(5)
                self.assertFalse(read_thread.is_alive())
                self.assertFalse(shutdown_thread.is_alive())
                self.assertNotIn("exc", read_result, read_result.get("exc"))
                self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
                self.assertEqual(shutdown_result["out"]["state"], "closed")
                self.assertTrue(read_result["out"]["ok"])
                close_test_app(app)
                self.assertFalse(root.exists())

    def test_shutdown_rejects_prepare_after_closing_begins(self):
        provider = BlockingSyntheticWavTTSProvider()
        prepare_hook_entered = threading.Event()
        release_prepare_hook = threading.Event()
        read_result: dict[str, object] = {}
        prepare_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}

        def run_read(app) -> None:
            try:
                read_result["out"] = app.read_current(play=False)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                read_result["exc"] = exc

        def run_prepare(app) -> None:
            try:
                prepare_result["out"] = app.prepare_document()
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                prepare_result["exc"] = exc

        def run_shutdown(app) -> None:
            try:
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.load_text("doc", "Doc", make_reading_document("Doc", 4), prefetch=False)

            def block_prepare_registration() -> None:
                prepare_hook_entered.set()
                release_prepare_hook.wait()

            with mock.patch.object(app, "_before_prepare_registration", side_effect=block_prepare_registration):
                read_thread = threading.Thread(target=run_read, args=(app,), name="read-thread")
                read_thread.start()
                self.assertTrue(provider.started.wait(5))

                prepare_thread = threading.Thread(target=run_prepare, args=(app,), name="prepare-thread")
                prepare_thread.start()
                self.assertTrue(prepare_hook_entered.wait(5))

                shutdown_thread = threading.Thread(target=run_shutdown, args=(app,), name="shutdown-thread")
                shutdown_thread.start()

                self.assertTrue(wait_until(lambda: app._background_work_state == "closing", timeout=5))
                release_prepare_hook.set()

                prepare_thread.join(5)
                self.assertFalse(prepare_thread.is_alive())
                self.assertNotIn("exc", prepare_result, prepare_result.get("exc"))
                self.assertEqual(prepare_result["out"]["error"], "service_shutting_down")
                self.assertEqual(app.prepare_status()["status"], "idle")
                self.assertIsNone(app._prepare_thread)

                provider.release.set()
                read_thread.join(5)
                shutdown_thread.join(5)
                self.assertFalse(read_thread.is_alive())
                self.assertFalse(shutdown_thread.is_alive())
                self.assertNotIn("exc", read_result, read_result.get("exc"))
                self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
                self.assertEqual(shutdown_result["out"]["state"], "closed")
                self.assertTrue(read_result["out"]["ok"])
                close_test_app(app)
                self.assertFalse(root.exists())

    def test_shutdown_rejects_interactive_tts_after_closing_begins(self):
        provider = SyntheticWavTTSProvider()
        read_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}

        def run_read(app) -> None:
            try:
                read_result["out"] = app.read_current(play=False)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                read_result["exc"] = exc

        def run_shutdown(app) -> None:
            try:
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.load_text("doc", "Doc", make_reading_document("Doc", 4), prefetch=False)
            app._tts_lock.acquire()
            try:
                read_thread = threading.Thread(target=run_read, args=(app,), name="read-thread")
                read_thread.start()
                self.assertTrue(wait_until(lambda: app._background_work_active_tts > 0, timeout=5))

                shutdown_thread = threading.Thread(target=run_shutdown, args=(app,), name="shutdown-thread")
                shutdown_thread.start()
                self.assertTrue(wait_until(lambda: app._background_work_state == "closing", timeout=5))
            finally:
                app._tts_lock.release()

            read_thread.join(5)
            shutdown_thread.join(5)
            self.assertFalse(read_thread.is_alive())
            self.assertFalse(shutdown_thread.is_alive())
            self.assertNotIn("exc", read_result, read_result.get("exc"))
            self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
            self.assertEqual(read_result["out"]["error"], "La lectura se detuvo porque el servicio se está cerrando.")
            self.assertEqual(provider.calls, [])
            self.assertEqual(shutdown_result["out"]["state"], "closed")
            close_test_app(app)
            self.assertFalse(root.exists())

    def test_shutdown_releases_background_tts_wait_without_querying_lifecycle_under_tts_gate(self):
        provider = SyntheticWavTTSProvider()
        synth_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}

        def run_synth(app) -> None:
            try:
                synth_result["out"] = app._synthesize_cached_with_settings(
                    "Texto de prueba para prefetch bloqueado.",
                    app.voice.voice,
                    app.voice.language,
                    prefetch_key=(app._document_generation, 0, app.voice.voice, app.voice.language, "blocked-prefetch"),
                )
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                synth_result["exc"] = exc

        def run_shutdown(app) -> None:
            try:
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=provider) as app:
            with app._tts_gate:
                app._interactive_tts_pending = 1
            with guard_background_work_queries(app):
                synth_thread = threading.Thread(target=run_synth, args=(app,), name="blocked-prefetch-thread")
                synth_thread.start()
                self.assertTrue(wait_until(lambda: app._background_work_active_tts > 0, timeout=5))

                shutdown_thread = threading.Thread(target=run_shutdown, args=(app,), name="shutdown-thread")
                shutdown_thread.start()
                self.assertTrue(wait_until(lambda: app._background_work_state == "closing", timeout=5))

                synth_thread.join(5)
                shutdown_thread.join(5)

            self.assertFalse(synth_thread.is_alive())
            self.assertFalse(shutdown_thread.is_alive())
            self.assertNotIn("exc", synth_result, synth_result.get("exc"))
            self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
            self.assertFalse(synth_result["out"].ok)
            self.assertEqual(synth_result["out"].detail, "shutdown_in_progress")
            self.assertEqual(provider.calls, [])
            self.assertEqual(shutdown_result["out"]["state"], "closed")

    def test_shutdown_releases_wait_for_interactive_tts_without_querying_lifecycle_under_tts_gate(self):
        wait_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}
        query_seen = threading.Event()

        def run_wait(app) -> None:
            try:
                app._wait_for_interactive_tts()
                wait_result["done"] = True
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                wait_result["exc"] = exc

        def run_shutdown(app) -> None:
            try:
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            with app._tts_gate:
                app._interactive_tts_pending = 1
            with guard_background_work_queries(app, query_seen=query_seen):
                wait_thread = threading.Thread(target=run_wait, args=(app,), name="wait-for-interactive-thread")
                wait_thread.start()
                self.assertTrue(query_seen.wait(5))
                wait_thread.join(0.05)
                self.assertTrue(wait_thread.is_alive())

                shutdown_thread = threading.Thread(target=run_shutdown, args=(app,), name="shutdown-thread")
                shutdown_thread.start()
                self.assertTrue(wait_until(lambda: app._background_work_state == "closing", timeout=5))

                wait_thread.join(5)
                shutdown_thread.join(5)

            self.assertFalse(wait_thread.is_alive())
            self.assertFalse(shutdown_thread.is_alive())
            self.assertNotIn("exc", wait_result, wait_result.get("exc"))
            self.assertTrue(wait_result.get("done"))
            self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
            self.assertEqual(shutdown_result["out"]["state"], "closed")

    def test_shutdown_and_clear_prefetch_queue_do_not_query_lifecycle_under_prefetch_lock(self):
        clear_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}

        def run_clear(app, barrier: threading.Barrier) -> None:
            try:
                barrier.wait()
                app._clear_prefetch_queue()
                clear_result["done"] = True
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                clear_result["exc"] = exc

        def run_shutdown(app, barrier: threading.Barrier) -> None:
            try:
                barrier.wait()
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            app._prefetch_lock = TrackingLock()
            future = Future()
            key = (app._document_generation, 0, app.voice.voice, app.voice.language, "prefetch-clear")
            with app._prefetch_lock:
                app._prefetch_futures[key] = future
                app._prefetch_started[key] = time.time()
                app._prefetch_future = future
                app._prefetch_index = 0
                app._prefetch_started_ts = time.time()
            with app._tts_gate:
                app._prefetch_promoted_keys.add(key)

            barrier = threading.Barrier(3)
            with guard_background_work_queries(app):
                clear_thread = threading.Thread(target=run_clear, args=(app, barrier), name="clear-prefetch-thread")
                shutdown_thread = threading.Thread(target=run_shutdown, args=(app, barrier), name="shutdown-thread")
                clear_thread.start()
                shutdown_thread.start()
                barrier.wait()
                clear_thread.join(5)
                shutdown_thread.join(5)

            self.assertFalse(clear_thread.is_alive())
            self.assertFalse(shutdown_thread.is_alive())
            self.assertNotIn("exc", clear_result, clear_result.get("exc"))
            self.assertTrue(clear_result.get("done"))
            self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
            self.assertEqual(shutdown_result["out"]["state"], "closed")
            self.assertFalse(app._prefetch_futures)
            self.assertIsNone(app._prefetch_future)
            self.assertTrue(future.cancelled())
            self.assertFalse(app._prefetch_promoted_keys)

    def test_shutdown_and_reset_prefetch_queue_do_not_query_lifecycle_under_prefetch_lock(self):
        reset_result: dict[str, object] = {}
        shutdown_result: dict[str, object] = {}

        def run_reset(app, barrier: threading.Barrier, stale_future: Future) -> None:
            try:
                barrier.wait()
                app._reset_prefetch_queue(stale_future)
                reset_result["done"] = True
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                reset_result["exc"] = exc

        def run_shutdown(app, barrier: threading.Barrier) -> None:
            try:
                barrier.wait()
                shutdown_result["out"] = app.shutdown_background_work(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_result["exc"] = exc

        with managed_test_app(tts=SyntheticWavTTSProvider()) as app:
            app._prefetch_lock = TrackingLock()
            stale_future = Future()
            key = (app._document_generation, 0, app.voice.voice, app.voice.language, "prefetch-reset")
            with app._prefetch_lock:
                app._prefetch_futures[key] = stale_future
                app._prefetch_started[key] = time.time()
                app._prefetch_future = stale_future
                app._prefetch_index = 0
                app._prefetch_started_ts = time.time()
            with app._tts_gate:
                app._prefetch_promoted_keys.add(key)

            barrier = threading.Barrier(3)
            with guard_background_work_queries(app):
                reset_thread = threading.Thread(
                    target=run_reset, args=(app, barrier, stale_future), name="reset-prefetch-thread"
                )
                shutdown_thread = threading.Thread(target=run_shutdown, args=(app, barrier), name="shutdown-thread")
                reset_thread.start()
                shutdown_thread.start()
                barrier.wait()
                reset_thread.join(5)
                shutdown_thread.join(5)

            self.assertFalse(reset_thread.is_alive())
            self.assertFalse(shutdown_thread.is_alive())
            self.assertNotIn("exc", reset_result, reset_result.get("exc"))
            self.assertTrue(reset_result.get("done"))
            self.assertNotIn("exc", shutdown_result, shutdown_result.get("exc"))
            self.assertEqual(shutdown_result["out"]["state"], "closed")
            self.assertTrue(stale_future.cancelled())
            self.assertFalse(app._prefetch_futures)
            self.assertIsNone(app._prefetch_future)
            self.assertFalse(app._prefetch_promoted_keys)

    def test_shutdown_timeout_can_be_retried_to_completion(self):
        provider = BlockingSyntheticWavTTSProvider()
        read_result: dict[str, object] = {}

        def run_read(app) -> None:
            try:
                read_result["out"] = app.read_current(play=False)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                read_result["exc"] = exc

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.load_text("doc", "Doc", make_reading_document("Doc", 4), prefetch=False)
            read_thread = threading.Thread(target=run_read, args=(app,), name="read-thread")
            read_thread.start()
            self.assertTrue(provider.started.wait(5))

            with self.assertRaises(AssertionError):
                app.shutdown_background_work(timeout=0.05)

            self.assertTrue(root.exists())
            self.assertEqual(app._background_work_state, "closing")
            self.assertFalse(app._background_work_closed)

            provider.release.set()
            read_thread.join(5)
            self.assertFalse(read_thread.is_alive())
            self.assertNotIn("exc", read_result, read_result.get("exc"))

            retry = app.shutdown_background_work(timeout=5)
            self.assertEqual(retry["state"], "closed")
            self.assertTrue(app._background_work_closed)
            self.assertEqual(app.shutdown_background_work(timeout=5)["detail"], "already_closed")
            close_test_app(app)
            self.assertFalse(root.exists())

    def test_concurrent_shutdown_reuses_single_close_path(self):
        provider = BlockingSyntheticWavTTSProvider()
        read_result: dict[str, object] = {}
        shutdown_results: list[dict[str, object]] = []
        shutdown_errors: list[Exception] = []

        def run_read(app) -> None:
            try:
                read_result["out"] = app.read_current(play=False)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                read_result["exc"] = exc

        def run_shutdown(app, barrier: threading.Barrier) -> None:
            try:
                barrier.wait()
                shutdown_results.append(app.shutdown_background_work(timeout=5))
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                shutdown_errors.append(exc)

        with managed_test_app(tts=provider) as app:
            root = Path(app._test_root)
            app.load_text("doc", "Doc", make_reading_document("Doc", 4), prefetch=False)
            read_thread = threading.Thread(target=run_read, args=(app,), name="read-thread")
            read_thread.start()
            self.assertTrue(provider.started.wait(5))

            barrier = threading.Barrier(3)
            shutdown_threads = [
                threading.Thread(target=run_shutdown, args=(app, barrier), name="shutdown-thread-1"),
                threading.Thread(target=run_shutdown, args=(app, barrier), name="shutdown-thread-2"),
            ]
            for thread in shutdown_threads:
                thread.start()
            barrier.wait()

            self.assertTrue(wait_until(lambda: app._background_work_state == "closing", timeout=5))
            provider.release.set()

            for thread in shutdown_threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
            read_thread.join(5)
            self.assertFalse(read_thread.is_alive())
            self.assertNotIn("exc", read_result, read_result.get("exc"))
            self.assertFalse(shutdown_errors, shutdown_errors)
            self.assertEqual(len(shutdown_results), 2)
            self.assertTrue(all(result["state"] == "closed" for result in shutdown_results))
            self.assertEqual(app._background_work_shutdown_context.started, True)
            self.assertEqual(
                len(app._background_work_shutdown_context.shutdown_threads),
                len(app._background_work_shutdown_context.executors),
            )
            close_test_app(app)
            self.assertFalse(root.exists())

    def test_background_shutdown_on_one_app_does_not_cancel_another(self):
        provider_a = BlockingSyntheticWavTTSProvider()
        app_a = test_app(tts=provider_a)
        app_b = test_app(tts=SyntheticWavTTSProvider())
        root_a = Path(app_a._test_root)
        root_b = Path(app_b._test_root)

        try:
            app_a.load_text("doc-a", "Doc A", make_reading_document("Doc A", 4), prefetch=True)
            self.assertTrue(provider_a.started.wait(5))
            self.assertTrue(app_a._prefetch_futures)

            app_b.load_text("doc-b", "Doc B", make_reading_document("Doc B", 2), prefetch=False)
            close_test_app(app_b)
            self.assertFalse(root_b.exists())
            self.assertTrue(root_a.exists())
            self.assertTrue(app_a._prefetch_futures)

            provider_a.release.set()
            close_test_app(app_a)
            self.assertFalse(root_a.exists())
        finally:
            provider_a.release.set()
            close_test_app(app_b)
            close_test_app(app_a)
            self.assertFalse(root_b.exists())
            self.assertFalse(root_a.exists())

    def test_managed_test_app_cleanup_runs_after_exception(self):
        provider = BlockingSyntheticWavTTSProvider()
        read_result: dict[str, object] = {}
        root_holder: dict[str, Path] = {}

        def run_read(app) -> None:
            try:
                read_result["out"] = app.read_current(play=False)
            except Exception as exc:  # pragma: no cover - surfaced by assertions below
                read_result["exc"] = exc

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with managed_test_app(tts=provider) as app:
                root_holder["root"] = Path(app._test_root)
                app.load_text("doc", "Doc", make_reading_document("Doc", 4), prefetch=False)
                read_thread = threading.Thread(target=run_read, args=(app,), name="read-thread")
                read_thread.start()
                self.assertTrue(provider.started.wait(5))
                try:
                    raise RuntimeError("boom")
                finally:
                    provider.release.set()
                    read_thread.join(5)
        self.assertTrue(root_holder["root"])
        self.assertFalse(root_holder["root"].exists())
        self.assertNotIn("exc", read_result, read_result.get("exc"))

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
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(b"\0" * 1600)
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

            def fake_run(cmd, **_kwargs):
                list_path = Path(cmd[cmd.index("-i") + 1])
                created_list_files.append(list_path)
                out.touch()
                return mock.Mock()

            with (
                mock.patch("fusion_reader_v2.audio_export.shutil.which", return_value="/usr/bin/ffmpeg"),
                mock.patch("fusion_reader_v2.audio_export.run_owned", side_effect=fake_run),
            ):
                method = concat_wav_files([first, second], out)
            self.assertEqual(method, "ffmpeg")
            self.assertTrue(out.exists())
            self.assertFalse(any(path.exists() for path in created_list_files))

    def test_audio_export_filename_sanitizer_blocks_path_traversal(self):
        self.assertEqual(sanitize_audio_title("../danger"), "danger")
