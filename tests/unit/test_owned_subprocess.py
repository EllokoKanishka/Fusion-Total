from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest

from fusion_reader_v2.owned_subprocess import OwnedProcessError, run_owned, sanitized_command


class OwnedSubprocessTests(unittest.TestCase):
    def test_success_error_and_bounded_output(self) -> None:
        success = run_owned([sys.executable, "-c", "print('ok')"], timeout=2, text=True)
        self.assertEqual((success.returncode, success.stdout.strip()), (0, "ok"))
        failed = run_owned([sys.executable, "-c", "raise SystemExit(7)"], timeout=2)
        self.assertEqual(failed.returncode, 7)
        large = run_owned([sys.executable, "-c", "print('x' * 10000)"], timeout=2, output_limit=128)
        self.assertEqual(len(large.stdout), 128)

    def test_timeout_reaps_process_that_ignores_sigterm(self) -> None:
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_owned(
                [
                    sys.executable,
                    "-c",
                    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
                ],
                timeout=0.15,
                terminate_grace=0.1,
            )
        self.assertLess(time.monotonic() - started, 2)

    def test_cancel_reaps_process(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(OwnedProcessError, "owned_process_cancelled"):
                run_owned([sys.executable, "-c", "import time; time.sleep(30)"], timeout=5, cancel_event=cancel)
        finally:
            timer.cancel()

    def test_command_logging_redacts_secrets(self) -> None:
        rendered = sanitized_command(["tool", "--token", "secret-value", "x" * 300])
        self.assertNotIn("secret-value", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertIn("<arg:300 chars>", rendered)


if __name__ == "__main__":
    unittest.main()
