import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class LauncherFunctionalTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.bin_dir = Path(self.tmp_dir) / "bin"
        self.bin_dir.mkdir()

        # Side effects log file
        self.side_effects_log = Path(self.tmp_dir) / "side_effects.log"
        self.side_effects_log.touch()

        # Healthy marker file
        self.healthy_marker = Path(self.tmp_dir) / "healthy_marker"

        # Mock curl
        self.mock_curl = self.bin_dir / "curl"
        self.mock_curl.write_text(
            f"""#!/usr/bin/env bash
url="${{!#}}"
if [[ "$url" == *"8021"* ]] || [[ "$url" == *"7853"* ]] || [[ "$url" == *"7851"* ]]; then
  exit 0
fi

if [[ -f "{self.healthy_marker}" ]]; then
  echo '{{"ok": true, "runtime": {{"app": "fusion_reader_v2", "commit": "f390f34"}}}}'
  exit 0
else
  exit 7
fi
""",
            encoding="utf-8",
        )
        self.mock_curl.chmod(0o755)

        # Mock xdg-open
        self.mock_xdg_open = self.bin_dir / "xdg-open"
        self.mock_xdg_open.write_text(
            f"""#!/usr/bin/env bash
echo "xdg-open $*" >> "{self.side_effects_log}"
""",
            encoding="utf-8",
        )
        self.mock_xdg_open.chmod(0o755)

        # Mock zenity
        self.mock_zenity = self.bin_dir / "zenity"
        self.mock_zenity.write_text(
            f"""#!/usr/bin/env bash
echo "zenity $*" >> "{self.side_effects_log}"
""",
            encoding="utf-8",
        )
        self.mock_zenity.chmod(0o755)

        # Mock notify-send
        self.mock_notify_send = self.bin_dir / "notify-send"
        self.mock_notify_send.write_text(
            f"""#!/usr/bin/env bash
echo "notify-send $*" >> "{self.side_effects_log}"
""",
            encoding="utf-8",
        )
        self.mock_notify_send.chmod(0o755)

        # Mock systemctl
        self.mock_systemctl = self.bin_dir / "systemctl"
        self.mock_systemctl.write_text(
            f"""#!/usr/bin/env bash
echo "systemctl $*" >> "{self.side_effects_log}"
if [[ "$1" == "--user" && "$2" == "show" ]]; then
  if [[ -f "{self.tmp_dir}/systemd_missing" ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$1" == "--user" && "$2" == "start" ]]; then
  if [[ -f "{self.tmp_dir}/systemctl_fail" ]]; then
    exit 1
  fi
  # Simulate the service starting the server by touching the healthy marker
  touch "{self.healthy_marker}"
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        self.mock_systemctl.chmod(0o755)

        # Mock systemd-run
        self.mock_systemd_run = self.bin_dir / "systemd-run"
        self.mock_systemd_run.write_text(
            f"""#!/usr/bin/env bash
echo "systemd-run $*" >> "{self.side_effects_log}"
""",
            encoding="utf-8",
        )
        self.mock_systemd_run.chmod(0o755)

        # Setup test environment PATH
        self.test_env = dict(os.environ)
        self.test_env["PATH"] = f"{self.bin_dir}:{self.test_env.get('PATH', '')}"
        self.test_env["FUSION_READER_V2_PORT"] = "19010"
        self.test_env["FUSION_READER_STARTUP_WAIT_SECONDS"] = "2"
        self.test_env["FUSION_READER_GPU_TTS_WAIT_SECONDS"] = "1"

        # Mock python runner script
        self.mock_python = Path(self.tmp_dir) / "mock_python.sh"
        self.test_env["FUSION_READER_PYTHON"] = str(self.mock_python)

        self.repo_root = Path(__file__).resolve().parents[1]

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_launcher_reuses_healthy_instance(self):
        # Simulator server already healthy
        self.healthy_marker.touch()

        self.mock_python.write_text(
            f"""#!/usr/bin/env bash
echo "python-should-not-run" >> "{self.side_effects_log}"
exit 1
""",
            encoding="utf-8",
        )
        self.mock_python.chmod(0o755)

        script_path = self.repo_root / "scripts" / "open_fusion_reader.sh"
        result = subprocess.run(
            [str(script_path)],
            cwd=str(self.repo_root),
            env=self.test_env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        time.sleep(0.5)
        log_content = self.side_effects_log.read_text(encoding="utf-8")
        self.assertIn("xdg-open http://127.0.0.1:19010/", log_content)
        self.assertNotIn("python-should-not-run", log_content)
        self.assertNotIn("systemctl --user start pandafusion.service", log_content)
        self.assertNotIn("zenity", log_content)

    def test_launcher_starts_via_systemctl_and_waits_for_healthcheck(self):
        # Mock python isn't executed directly by script, systemctl handles it
        self.mock_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.mock_python.chmod(0o755)

        script_path = self.repo_root / "scripts" / "open_fusion_reader.sh"
        result = subprocess.run(
            [str(script_path)],
            cwd=str(self.repo_root),
            env=self.test_env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        time.sleep(0.5)
        log_content = self.side_effects_log.read_text(encoding="utf-8")
        self.assertIn("systemctl --user start pandafusion.service", log_content)
        self.assertIn("xdg-open http://127.0.0.1:19010/", log_content)
        self.assertNotIn("zenity", log_content)

    def test_launcher_fails_and_does_not_open_browser(self):
        # systemctl will fail
        (Path(self.tmp_dir) / "systemctl_fail").touch()

        script_path = self.repo_root / "scripts" / "open_fusion_reader.sh"
        result = subprocess.run(
            [str(script_path)],
            cwd=str(self.repo_root),
            env=self.test_env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        log_content = self.side_effects_log.read_text(encoding="utf-8")
        self.assertNotIn("xdg-open", log_content)
        self.assertTrue("zenity" in log_content or "notify-send" in log_content)

    def test_launcher_fallback_traditional_script(self):
        # simulate systemd not available
        (Path(self.tmp_dir) / "systemd_missing").touch()

        # Mock python script that touches healthy marker representing successful startup via fallback
        self.mock_python.write_text(
            f"""#!/usr/bin/env bash
if [[ "$*" == *"-c"* ]]; then
  exit 0
fi
echo "python-started-fallback" >> "{self.side_effects_log}"
touch "{self.healthy_marker}"
sleep 1
exit 0
""",
            encoding="utf-8",
        )
        self.mock_python.chmod(0o755)

        script_path = self.repo_root / "scripts" / "open_fusion_reader.sh"
        result = subprocess.run(
            [str(script_path)],
            cwd=str(self.repo_root),
            env=self.test_env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            print("SIDE EFFECTS:", self.side_effects_log.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0)
        time.sleep(0.5)
        log_content = self.side_effects_log.read_text(encoding="utf-8")
        self.assertIn("python-started-fallback", log_content)
        self.assertIn("xdg-open http://127.0.0.1:19010/", log_content)

    def test_installed_service_uses_voice_first_systemd_entrypoint(self):
        installer = (self.repo_root / "scripts" / "install_launcher.sh").read_text(encoding="utf-8")
        entrypoint = (self.repo_root / "scripts" / "start_pandafusion_systemd.sh").read_text(encoding="utf-8")

        self.assertIn('ExecStart="$ROOT/scripts/start_pandafusion_systemd.sh"', installer)
        self.assertIn("KillMode=control-group", installer)
        self.assertIn("start_reader_neural_tts_gpu_5090.sh", entrypoint)
        self.assertIn("start_reader_neural_tts.sh", entrypoint)
        self.assertLess(
            entrypoint.index("start_reader_neural_tts_gpu_5090.sh"),
            entrypoint.index("start_reader_neural_tts.sh"),
        )
        self.assertIn('export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"', entrypoint)
        self.assertIn('export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"', entrypoint)
        self.assertIn("-m scripts.fusion_reader_v2_server", entrypoint)

    def test_systemd_entrypoint_selects_ready_cpu_fallback_before_web_server(self):
        self.mock_python.write_text(
            f'''#!/usr/bin/env bash
echo "tts-url=$FUSION_READER_ALLTALK_URL args=$*" >> "{self.side_effects_log}"
exit 0
''',
            encoding="utf-8",
        )
        self.mock_python.chmod(0o755)
        runtime = Path(self.tmp_dir) / "runtime"
        environment = {
            **self.test_env,
            "FUSION_READER_RUNTIME_ROOT": str(runtime),
            "FUSION_READER_LOG_ROOT": str(runtime / "logs"),
        }

        result = subprocess.run(
            [str(self.repo_root / "scripts" / "start_pandafusion_systemd.sh")],
            cwd=str(self.repo_root),
            env=environment,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        log_content = self.side_effects_log.read_text(encoding="utf-8")
        self.assertIn("tts-url=http://127.0.0.1:7851", log_content)
        self.assertIn("-m scripts.fusion_reader_v2_server", log_content)


if __name__ == "__main__":
    unittest.main()
