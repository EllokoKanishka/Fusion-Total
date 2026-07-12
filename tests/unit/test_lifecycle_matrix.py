from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import Future

from fusion_reader_v2.audio_export import AudioExportJob
from fusion_reader_v2.services.lifecycle import BackgroundShutdownContext
from fusion_reader_v2.tts import AudioArtifact
from tests.helpers import close_test_app, test_app


class LifecycleBranchMatrixTests(unittest.TestCase):
    def test_state_tts_counters_and_wait_timeout(self) -> None:
        app = test_app(register_cleanup=False)
        lifecycle = app._lifecycle_service
        try:
            with app._background_work_condition:
                with self.assertRaises(ValueError):
                    lifecycle.set_state_locked("invalid")
                lifecycle.set_state_locked("open")
            self.assertTrue(lifecycle.is_open())
            self.assertTrue(lifecycle.begin_tts_operation())
            with app._background_work_condition:
                with self.assertRaisesRegex(AssertionError, "interactive TTS"):
                    lifecycle.wait_for_active_tts_locked(time.monotonic())
            lifecycle.end_tts_operation()
            lifecycle.end_tts_operation()
            with app._background_work_condition:
                lifecycle.set_state_locked("closing")
            self.assertFalse(lifecycle.begin_tts_operation())
            self.assertFalse(lifecycle.is_open_locked())
        finally:
            with app._background_work_condition:
                lifecycle.set_state_locked("open")
            close_test_app(app)

    def test_capture_and_prioritize_updates_running_jobs(self) -> None:
        app = test_app(register_cleanup=False)
        try:
            job = AudioExportJob("job", state="running", detail="working")
            app._audio_export_jobs[job.job_id] = job
            app._audio_export_active_job_id = job.job_id
            app._prepare_status = {"status": "running"}
            context = BackgroundShutdownContext()
            app._lifecycle_service.capture_shutdown_context(context)
            self.assertEqual(job.state, "canceling")
            self.assertEqual(app._prepare_status["status"], "canceling")

            app._prepare_cancel.clear()
            app._prepare_status = {"status": "running"}
            app._lifecycle_service.prioritize_dialogue()
            self.assertTrue(app._prepare_cancel.is_set())
            self.assertEqual(app._prepare_status["status"], "canceling")
        finally:
            close_test_app(app)

    def test_clear_and_reset_prefetch_queues_cover_open_closed_and_untracked(self) -> None:
        app = test_app(register_cleanup=False)
        try:
            first: Future[AudioArtifact] = Future()
            key = (1, 0, "text", "voice", "es")
            with app._prefetch_lock:
                app._prefetch_futures[key] = first
                app._prefetch_future = first
                app._prefetch_index = 0
            app._prefetch_promoted_keys.add(key)
            app._lifecycle_service.reset_prefetch_queue(first)
            self.assertTrue(first.cancelled())
            self.assertNotIn(key, app._prefetch_promoted_keys)

            untracked: Future[AudioArtifact] = Future()
            app._lifecycle_service.reset_prefetch_queue(untracked)
            self.assertFalse(untracked.cancelled())

            second: Future[AudioArtifact] = Future()
            with app._prefetch_lock:
                app._prefetch_futures[key] = second
                app._prefetch_future = second
            with app._background_work_condition:
                app._lifecycle_service.set_state_locked("closing")
            app._lifecycle_service.reset_prefetch_queue(second)
            self.assertTrue(second.cancelled())
            with app._background_work_condition:
                app._lifecycle_service.set_state_locked("open")

            third: Future[AudioArtifact] = Future()
            with app._prefetch_lock:
                app._prefetch_futures[key] = third
            app._lifecycle_service.clear_prefetch_queue()
            self.assertTrue(third.cancelled())
        finally:
            close_test_app(app)

    def test_wait_for_thread_none_deadline_and_live_timeout(self) -> None:
        lifecycle = test_app(register_cleanup=False)._lifecycle_service
        app = lifecycle.owner
        release = threading.Event()
        thread = threading.Thread(target=release.wait, name="fusion-lifecycle-matrix")
        thread.start()
        try:
            lifecycle.wait_for_thread(None, label="none", deadline=time.monotonic())
            with self.assertRaisesRegex(AssertionError, "waiting for deadline"):
                lifecycle.wait_for_thread(thread, label="deadline", deadline=time.monotonic())
            with self.assertRaisesRegex(AssertionError, "thread to stop"):
                lifecycle.wait_for_thread(thread, label="alive", deadline=time.monotonic() + 0.01)
            release.set()
            lifecycle.wait_for_thread(thread, label="done", deadline=time.monotonic() + 1.0)
        finally:
            release.set()
            thread.join(1.0)
            close_test_app(app)

    def test_shutdown_surfaces_recorded_executor_error_then_can_retry(self) -> None:
        app = test_app(register_cleanup=False)
        context = BackgroundShutdownContext(started=True, shutdown_errors=[("executor-0", RuntimeError("boom"))])
        app._background_work_shutdown_context = context
        with app._background_work_condition:
            app._lifecycle_service.set_state_locked("closing")
        try:
            with self.assertRaisesRegex(AssertionError, "executor-0"):
                app.shutdown_background_work(timeout=1.0)
        finally:
            context.shutdown_errors.clear()
            close_test_app(app)


if __name__ == "__main__":
    unittest.main()
