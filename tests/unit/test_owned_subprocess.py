from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from fusion_reader_v2 import owned_subprocess
from fusion_reader_v2.owned_subprocess import OwnedProcessError, run_owned, sanitized_command, terminate_owned_process


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

    def test_shell_input_and_signal_failure_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "shell_not_supported"):
            run_owned([sys.executable, "-c", "pass"], timeout=1, shell=True)
        echoed = run_owned(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            timeout=2,
            input="hola",
            text=True,
        )
        self.assertEqual(echoed.stdout, "hola")

        finished = mock.Mock()
        finished.poll.return_value = 0
        with mock.patch.object(owned_subprocess.os, "killpg") as killpg:
            owned_subprocess._signal_process(finished, 15)
        killpg.assert_not_called()

        fallback = mock.Mock(pid=123)
        fallback.poll.return_value = None
        with mock.patch.object(owned_subprocess.os, "killpg", side_effect=PermissionError):
            owned_subprocess._signal_process(fallback, 15)
        fallback.send_signal.assert_called_once_with(15)

        gone = mock.Mock(pid=123)
        gone.poll.return_value = None
        gone.send_signal.side_effect = ProcessLookupError
        with mock.patch.object(owned_subprocess.os, "killpg", side_effect=ProcessLookupError):
            owned_subprocess._signal_process(gone, 15)

    def test_unreaped_process_has_stable_error(self) -> None:
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("child", 0.1)
        with (
            mock.patch.object(owned_subprocess.os, "killpg"),
            self.assertRaisesRegex(OwnedProcessError, "owned_process_unreaped"),
        ):
            terminate_owned_process(process, grace=0.01)


if __name__ == "__main__":
    unittest.main()
