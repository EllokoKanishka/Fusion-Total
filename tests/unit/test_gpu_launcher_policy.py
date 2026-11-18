import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class GPULauncherPolicyTests(unittest.TestCase):
    def _run(self, *, occupied, game):
        root = Path(tempfile.mkdtemp())
        fake_python = root / "fake-python"
        fake_python.write_text('#!/bin/sh\nprintf \'{"measured":true}\\n\'\nexit "$PANDA_TEST_GPU_CODE"\n')
        fake_ps = root / "ps"
        fake_ps.write_text('#!/bin/sh\nprintf "%s\\n" "$PANDA_TEST_PROCESS"\n')
        for file in (fake_python, fake_ps):
            file.chmod(0o755)
        script = root / "gpu-policy-fixture.sh"
        guard = Path.cwd() / "scripts/fusion_reader_gpu_guard.sh"
        script.write_text(f"""#!/bin/bash
set -eu
source '{guard}'
export FUSION_READER_CHAT_THINK=1
export FUSION_READER_CHAT_NUM_CTX=32768
export FUSION_READER_CHAT_NUM_PREDICT=4096
export FUSION_READER_PREFETCH_AHEAD=3
fusion_reader_refuse_when_gpu_conflict 'test-component'
fusion_reader_apply_game_coexistence_mode || true
printf '%s %s %s %s\\n' "$FUSION_READER_CHAT_THINK" "$FUSION_READER_CHAT_NUM_CTX" "$FUSION_READER_CHAT_NUM_PREDICT" "$FUSION_READER_PREFETCH_AHEAD"
""")
        return subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            timeout=3,
            env={
                **os.environ,
                "PATH": str(root) + ":/usr/bin:/bin",
                "FUSION_READER_PYTHON": str(fake_python),
                "PANDA_TEST_GPU_CODE": "2" if occupied else "0",
                "PANDA_TEST_PROCESS": "123 bg3 /games/bg3" if game else "123 unknown-renderer",
                "FUSION_READER_GPU_CONFLICT_POLICY": "warn",
            },
        )

    def test_pressure_without_a_known_game_reduces_only_prefetch(self):
        result = self._run(occupied=True, game=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "1 32768 4096 0")
        self.assertIn("budget", result.stderr.lower())

    def test_known_game_with_available_budget_does_not_force_degradation(self):
        result = self._run(occupied=False, game=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "1 32768 4096 3")
