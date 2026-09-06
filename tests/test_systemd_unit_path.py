import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "scripts" / "systemd_unit_path.py"

spec = importlib.util.spec_from_file_location("systemd_unit_path", HELPER_PATH)
assert spec is not None and spec.loader is not None
systemd_unit_path = importlib.util.module_from_spec(spec)
spec.loader.exec_module(systemd_unit_path)


class SystemdUnitPathTests(unittest.TestCase):
    def test_escape_systemd_path_preserves_absolute_path_and_encodes_unsafe_bytes(self):
        escaped = systemd_unit_path.escape_systemd_path("/home/example/Escritorio/Fusión Total")
        self.assertEqual(
            escaped,
            "/home/example/Escritorio/Fusi\\xc3\\xb3n\\x20Total",
        )

    def test_escape_systemd_path_rejects_relative_paths(self):
        with self.assertRaises(ValueError):
            systemd_unit_path.escape_systemd_path("Fusion Total")

    def test_installer_uses_escaped_paths_for_path_only_systemd_directives(self):
        installer = (REPO_ROOT / "scripts" / "install_launcher.sh").read_text(encoding="utf-8")

        self.assertIn('SYSTEMD_ROOT="$("$PYTHON_BIN" "$ROOT/scripts/systemd_unit_path.py" "$ROOT")"', installer)
        self.assertIn("EnvironmentFile=$SYSTEMD_ROOT/.env", installer)
        self.assertIn("WorkingDirectory=$SYSTEMD_ROOT", installer)
        self.assertIn('ExecStart="$ROOT/scripts/start_pandafusion_systemd.sh"', installer)
        self.assertNotIn('EnvironmentFile="$ROOT/.env"', installer)
        self.assertNotIn('WorkingDirectory="$ROOT"', installer)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_systemd_analyze_accepts_generated_style_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Fusion Total"
            root.mkdir()
            (root / ".env").write_text("FUSION_TEST=1\n", encoding="utf-8")
            start = root / "start.sh"
            start.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            start.chmod(0o755)

            escaped_root = systemd_unit_path.escape_systemd_path(str(root))
            unit = Path(temp_dir) / "pandafusion-test.service"
            unit.write_text(
                "\n".join(
                    [
                        "[Unit]",
                        "Description=PandaFusion path escaping regression test",
                        "[Service]",
                        "Type=simple",
                        f"EnvironmentFile={escaped_root}/.env",
                        f"WorkingDirectory={escaped_root}",
                        f'ExecStart="{root}/start.sh"',
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
