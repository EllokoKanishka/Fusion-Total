from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import SyntheticWavTTSProvider, close_test_app, test_app, wait_for_audio_export


DOCUMENT = "\n\n".join(f"Bloque {index}. Texto sintético para verificar navegación estable." for index in range(1, 8))


class RepetitionStressTests(unittest.TestCase):
    def test_load_clear_and_navigation_cycles_are_bounded(self) -> None:
        before = {thread.ident for thread in threading.enumerate()}
        app = test_app(register_cleanup=False)
        try:
            for cycle in range(100):
                loaded = app.load_text(f"doc-{cycle}", f"Documento {cycle}", DOCUMENT, prefetch=False)
                self.assertTrue(loaded["ok"])
                app.clear_document()
            app.load_text("navigation", "Navegación", DOCUMENT, prefetch=False)
            for cycle in range(100):
                app.jump((cycle % app.status()["total"]) + 1)
                app.next()
                app.previous()
        finally:
            close_test_app(app)
        leaked = [
            thread.name
            for thread in threading.enumerate()
            if thread.ident not in before and thread.name.startswith("fusion-")
        ]
        self.assertEqual(leaked, [])

    def test_export_and_prepare_cancel_cycles_leave_no_live_jobs(self) -> None:
        provider = SyntheticWavTTSProvider(delay_seconds=0.003)
        app = test_app(tts=provider, register_cleanup=False)
        try:
            app.load_text("stress", "Stress", DOCUMENT, prefetch=False)
            for _ in range(50):
                export = app.start_audio_export("full")
                app.cancel_audio_export(export["job_id"])
                final = wait_for_audio_export(app, export["job_id"], timeout=5.0)
                self.assertIn(final["state"], {"cancelled", "done"})
            for _ in range(50):
                app.prepare_document()
                app.cancel_prepare()
                deadline = time.monotonic() + 2.0
                while app.prepare_status()["status"] in {"running", "canceling"} and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertIn(app.prepare_status()["status"], {"canceled", "done"})
            registry = app._audio_export_service.registry
            self.assertLessEqual(len(registry), registry.max_items)
        finally:
            close_test_app(app)

    def test_shutdown_is_idempotent_across_twenty_apps_and_does_not_resurrect_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fusion_shutdown_stress_") as temp:
            parent = Path(temp)
            for cycle in range(20):
                root = parent / str(cycle)
                app = test_app(root=root, register_cleanup=False)
                app.load_text("shutdown", "Shutdown", DOCUMENT, prefetch=True)
                first = app.shutdown_background_work(timeout=10.0)
                second = app.shutdown_background_work(timeout=10.0)
                self.assertTrue(first["ok"])
                self.assertTrue(second["ok"])
                marker = root / "must-not-reappear"
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
