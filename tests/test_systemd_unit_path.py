import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SystemdUnitPathTests(unittest.TestCase):
    def test_installer_lets_launcher_own_working_directory_and_dotenv(self):
        installer = (REPO_ROOT / "scripts" / "install_launcher.sh").read_text(encoding="utf-8")
        self.assertIn('ExecStart=/usr/bin/bash "$ROOT/scripts/start_pandafusion_systemd.sh"', installer)
        self.assertNotIn("EnvironmentFile=", installer)
        self.assertNotIn("WorkingDirectory=", installer)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_systemd_analyze_accepts_generated_style_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Fusion Total"
            root.mkdir()
            start = root / "start.sh"
            start.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            start.chmod(0o755)

            unit = Path(temp_dir) / "pandafusion-test.service"
            unit.write_text(
                "\n".join(
                    [
                        "[Unit]",
                        "Description=PandaFusion path escaping regression test",
                        "[Service]",
                        "Type=simple",
                        f'ExecStart=/usr/bin/bash "{root}/start.sh"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["systemd-analyze", "verify", str(unit)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
