import subprocess
import unittest
from pathlib import Path


class BusyControlHarnessTests(unittest.TestCase):
    def test_busy_control_harness_passes(self):
        root = Path(__file__).resolve().parents[1]
        harness = root / "tests" / "busy_controls.test.js"
        result = subprocess.run(
            ["node", str(harness)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"busy control harness failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("busy-controls: ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
