from __future__ import annotations

import os
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fusion_reader_v2.desktop_lifetime import DesktopLifetime, _process_matches, _stop_process, release_gpu_resources


class DesktopLifetimeTests(unittest.TestCase):
    def test_last_window_only_reload_grace_and_expired_clients(self):
        now = [0.0]
        shutdown = Mock()
        life = DesktopLifetime(shutdown, grace=4, ttl=180, clock=lambda: now[0])
        a, b = "a" * 36, "b" * 36
        self.assertFalse(life.tick())  # Boot alone does not arm shutdown.
        life.update(a)
        life.update(b)
        life.update(a, leaving=True)
        now[0] = 10
        self.assertFalse(life.tick())
        life.update(b, leaving=True)
        self.assertFalse(life.tick())
        now[0] = 12
        life.update(a)  # Reload/reopen cancels closing.
        now[0] = 190
        self.assertFalse(life.tick())
        now[0] = 193
        self.assertFalse(life.tick())  # Crashed window expires.
        now[0] = 198
        self.assertTrue(life.tick())
        self.assertFalse(life.tick())
        shutdown.assert_called_once()
        self.assertEqual(life.update(b)["state"], "closing")

    def test_never_claims_unrelated_process_and_stops_owned_child(self):
        self.assertFalse(_process_matches(os.getpid(), marker="python"))
        self.assertFalse(_process_matches(1, marker="fusion_reader_v2_stt_server.py"))
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; print('READY',flush=True); time.sleep(60)", "panda-test-owned"],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "READY")
            self.assertTrue(_process_matches(process.pid, marker="panda-test-owned"))
            self.assertFalse(_process_matches(process.pid, marker="tts_server:app", port=7853))
            self.assertTrue(_stop_process(process.pid))
            process.wait(timeout=3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    def test_cleanup_never_stops_lucy_or_ollama_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                providers=SimpleNamespace(tts_owner_file=Path(tmp) / "owner.json"),
                ports=SimpleNamespace(stt=8021),
                paths=SimpleNamespace(repository=Path(tmp)),
            )
            settings.providers.tts_owner_file.write_text('{"owner":"doctora-lucy","port":7854,"owner_pid":1234}')
            app = SimpleNamespace(
                conversation=SimpleNamespace(provider=SimpleNamespace(providers={})), dictation_assistant=None
            )
            with patch(
                "fusion_reader_v2.desktop_lifetime.run_owned",
                return_value=SimpleNamespace(stdout='users:(("python",pid=1234,fd=1))'),
            ):
                with (
                    patch("fusion_reader_v2.desktop_lifetime._process_matches", return_value=False),
                    patch("fusion_reader_v2.desktop_lifetime._stop_process") as stop,
                ):
                    result = release_gpu_resources(app, settings)
            stop.assert_not_called()
            self.assertFalse(result["tts_stopped"])
            self.assertFalse(result["stt_stopped"])
