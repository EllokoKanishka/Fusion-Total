import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fusion_reader_v2 import AudioArtifact, AudioCache
from fusion_reader_v2.owned_subprocess import OwnedProcessError, run_owned
from fusion_reader_v2.services.lifecycle import BackgroundLifecycleService
from fusion_reader_v2.web.server import FusionHTTPServer
from tests.helpers import test_app


class ShutdownSafetyTests(unittest.TestCase):
    def test_large_stdin_obeys_deadline_when_child_does_not_read(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_owned(
                [sys.executable, "-c", "import time; time.sleep(1.2)"],
                input=b"x" * 2_000_000,
                timeout=0.1,
                terminate_grace=0.1,
            )
        self.assertLess(time.monotonic() - started, 0.9)

    def test_large_stdin_can_be_cancelled_and_child_is_reaped(self):
        cancel = threading.Event()
        timer = threading.Timer(0.08, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(OwnedProcessError, "cancelled"):
                run_owned(
                    [sys.executable, "-c", "import time; time.sleep(1.2)"],
                    input=b"x" * 2_000_000,
                    timeout=3,
                    cancel_event=cancel,
                    terminate_grace=0.1,
                )
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 0.9)

    def test_output_storage_is_bounded_while_child_is_running(self):
        real_temporary_file = tempfile.TemporaryFile
        handles = []
        sizes = []

        def opened(*args, **kwargs):
            handle = real_temporary_file(*args, **kwargs)
            handles.append(handle)
            return handle

        def sample():
            for handle in handles:
                if not handle.closed:
                    sizes.append(os.fstat(handle.fileno()).st_size)

        timer = threading.Timer(0.2, sample)
        timer.start()
        try:
            with patch("tempfile.TemporaryFile", side_effect=opened):
                result = run_owned(
                    [
                        sys.executable,
                        "-c",
                        "import os,time; os.write(1,b'x'*8000000); os.write(2,b'y'*8000000); time.sleep(0.4)",
                    ],
                    timeout=2,
                    output_limit=4096,
                )
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(len(result.stdout), 4096)
        self.assertEqual(len(result.stderr), 4096)
        self.assertLessEqual(max(sizes, default=0), 4096)

    def test_idle_prefetch_resets_do_not_accumulate_executors(self):
        app = test_app()
        for _ in range(25):
            app._clear_prefetch_queue()
        self.assertLessEqual(len(app._prefetch_executors), 2)

    def test_busy_prefetch_rotations_remain_bounded_and_are_retained_for_shutdown(self):
        app = test_app()
        release = threading.Event()
        futures = []
        try:
            for index in range(12):
                with app._prefetch_lock:
                    future = app._executor.submit(release.wait, 2)
                    futures.append(future)
                    app._prefetch_futures[(index,)] = future
                    if hasattr(app, "_prefetch_executor_futures"):
                        app._prefetch_executor_futures.setdefault(app._executor, set()).add(future)
                app._clear_prefetch_queue()
            self.assertLessEqual(len(app._prefetch_executors), max(2, app.prefetch_workers + 1))
        finally:
            release.set()
            for future in futures:
                if not future.cancelled():
                    future.result(3)

    def test_lifecycle_requests_all_cancellations_when_first_phase_raises(self):
        prepare = Mock()
        prepare.begin_shutdown.return_value = None
        capture = Mock(return_value=([], []))
        service = BackgroundLifecycleService(
            capture_prefetch=capture, clear_prefetch=lambda: None, reset_prefetch=lambda f: None
        )
        service.bind_background_services(
            audio_export=SimpleNamespace(begin_shutdown=Mock(side_effect=RuntimeError("export failed"))),
            preparation=prepare,
        )
        with self.assertRaises(Exception):
            service.shutdown(timeout=0.2)
        prepare.begin_shutdown.assert_called_once()
        capture.assert_called_once()
        self.assertEqual(service.state, "closing")

    def test_failed_prefetch_does_not_skip_other_shutdown_waits(self):
        failed = Future()
        failed.set_exception(RuntimeError("provider failed"))
        waited = Mock()
        waited.done.return_value = False
        waited.cancel.return_value = False
        service = BackgroundLifecycleService(
            capture_prefetch=lambda: ([], [failed, waited]), clear_prefetch=lambda: None, reset_prefetch=lambda f: None
        )
        service.bind_background_services(
            audio_export=SimpleNamespace(begin_shutdown=lambda: None),
            preparation=SimpleNamespace(begin_shutdown=lambda: None),
        )
        try:
            service.shutdown(timeout=1)
        except Exception:
            pass
        # Completed failures are terminal, but never prevent waiting for others.
        waited.result.assert_called_once()

    def test_server_requests_cancellation_before_joining_http_threads(self):
        events = []
        app = SimpleNamespace(shutdown_background_work=lambda **kw: events.append("app") or {"ok": True})
        server = object.__new__(FusionHTTPServer)
        server.context = SimpleNamespace(
            app=app,
            request_shutdown_jobs=lambda: events.append("cancel"),
            shutdown_jobs=lambda **kw: events.append("jobs") or {"ok": True},
        )
        with (
            patch("http.server.ThreadingHTTPServer.server_close", side_effect=lambda *a: events.append("http")),
            patch("http.server.HTTPServer.server_close", side_effect=lambda *a: events.append("http")),
        ):
            server.server_close()
        self.assertLess(events.index("cancel"), events.index("http"))

    def test_server_still_closes_socket_and_app_after_job_shutdown_failure(self):
        events = []

        def fail(**kw):
            events.append("jobs")
            raise RuntimeError("jobs failed")

        app = SimpleNamespace(shutdown_background_work=lambda **kw: events.append("app") or {"ok": True})
        server = object.__new__(FusionHTTPServer)
        server.context = SimpleNamespace(
            app=app, request_shutdown_jobs=lambda: events.append("cancel"), shutdown_jobs=fail
        )
        with (
            patch("http.server.ThreadingHTTPServer.server_close", side_effect=lambda *a: events.append("http")),
            patch("http.server.HTTPServer.server_close", side_effect=lambda *a: events.append("http")),
        ):
            with self.assertRaises(RuntimeError):
                server.server_close()
        self.assertIn("app", events)
        self.assertIn("http", events)

    def test_cache_consumes_only_explicitly_owned_temporary(self):
        root = Path(tempfile.mkdtemp())
        cache = AudioCache(root / "cache")
        for owned in (False, True):
            source = root / f"source-{owned}.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 80)
            artifact = AudioArtifact(True, path=source, owned_temporary=owned)
            result = cache.put(str(owned), "v", "es", artifact)
            self.assertTrue(result.path.is_file())
            self.assertEqual(source.exists(), not owned)
            self.assertFalse(result.owned_temporary)

    def test_main_cleanup_runs_all_phases_after_close_exception(self):
        from fusion_reader_v2.web.server import _close_application

        lifetime = SimpleNamespace(stop=Mock(side_effect=RuntimeError("watcher failed")))
        server = SimpleNamespace(server_close=Mock(side_effect=RuntimeError("HTTP failed")))
        app = SimpleNamespace(shutdown_background_work=Mock())
        release = Mock()
        with self.assertRaisesRegex(RuntimeError, "shutdown_failed"):
            _close_application(server, app, lifetime, release)
        lifetime.stop.assert_called_once()
        server.server_close.assert_called_once()
        app.shutdown_background_work.assert_called_once()
        release.assert_called_once()

    def test_segmented_audio_never_deletes_unowned_provider_sources(self):
        from fusion_reader_v2 import NullTTSProvider

        root = Path(tempfile.mkdtemp())
        original = root / "persistent.wav"
        with wave.open(str(original), "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80)
        provider = NullTTSProvider()
        provider.max_input_chars = 80
        provider.synthesize = lambda *a, **kw: AudioArtifact(True, path=original)
        app = test_app(tts=provider)
        result = app._synthesize_segmented_with_settings("Texto de prueba. " * 30, "v", "es")
        try:
            self.assertTrue(result.ok)
            self.assertTrue(original.exists())
            self.assertTrue(result.owned_temporary)
        finally:
            result.path.unlink(missing_ok=True)

    def test_failed_cache_write_cleans_only_the_owned_provider_temp(self):
        from fusion_reader_v2 import NullTTSProvider

        provider = NullTTSProvider()
        app = test_app(tts=provider)
        artifact = provider.synthesize("Temporal propio")
        provider.synthesize = lambda *a, **kw: artifact
        with patch.object(app.cache, "put", side_effect=OSError("fixture cache failure")):
            with self.assertRaises(OSError):
                app._synthesize_cached("No está cacheado")
        self.assertFalse(artifact.path.exists())

    def test_failed_segment_concat_does_not_leave_an_owned_output(self):
        from fusion_reader_v2 import NullTTSProvider

        provider = NullTTSProvider()
        artifact = provider.synthesize("Tramo propio")
        provider.max_input_chars = 80
        provider.synthesize = lambda *a, **kw: artifact
        app = test_app(tts=provider)
        before = set(Path(tempfile.gettempdir()).glob("fusion_reader_v2_segmented_*.wav"))
        with patch("fusion_reader_v2.facade.concat_wav_files", side_effect=OSError("concat fixture")):
            with self.assertRaises(OSError):
                app._synthesize_segmented_with_settings("Texto de prueba. " * 30, "v", "es")
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("fusion_reader_v2_segmented_*.wav")), before)
        self.assertFalse(artifact.path.exists())

    def test_real_pending_http_is_cancelled_before_worker_join(self):
        import json
        import urllib.request
        from dataclasses import replace
        from fusion_reader_v2.config import create_settings
        from fusion_reader_v2.web.server import create_http_server

        root = Path(tempfile.mkdtemp())
        settings = create_settings(
            repository_root=Path.cwd(),
            environ={
                "HOME": str(root / "home"),
                "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
            },
        )
        settings = replace(settings, ports=replace(settings.ports, api=0))
        app = test_app()
        entered, release = threading.Event(), threading.Event()
        cancelled_while_pending, client_errors = [], []

        def chat(*a, **kw):
            entered.set()
            release.wait(3)
            return {"ok": True, "answer": "Respuesta de prueba"}

        app.chat = chat
        server = create_http_server(app, settings)
        serve = threading.Thread(target=server.serve_forever)
        serve.start()
        original_cancel = getattr(server.context, "request_shutdown_jobs", lambda: None)

        def request_cancel():
            cancelled_while_pending.append(not release.is_set())
            release.set()
            return original_cancel()

        server.context.request_shutdown_jobs = request_cancel

        def request():
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/chat",
                    data=b'{"message":"Pregunta sintetica"}',
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    self.assertTrue(json.load(response)["ok"])
            except Exception as exc:
                client_errors.append(exc)

        client = threading.Thread(target=request)
        client.start()
        try:
            self.assertTrue(entered.wait(2))
            server.shutdown()
            server.server_close()
            client.join(2)
            self.assertTrue(cancelled_while_pending and cancelled_while_pending[0])
            self.assertFalse(client.is_alive())
            self.assertEqual(client_errors, [])
        finally:
            release.set()
            server.shutdown()
            serve.join(3)
            client.join(3)
            server.server_close()
