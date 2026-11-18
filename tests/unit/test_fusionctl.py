from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2.config import create_settings
from scripts import fusionctl


class FusionCtlTests(unittest.TestCase):
    def _settings(self, root: Path):
        return create_settings(
            repository_root=root,
            environ={
                "HOME": str(root / "home"),
                "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
            },
        )

    def test_parser_exposes_required_commands(self) -> None:
        parser = fusionctl.build_parser()
        for argv in (
            ["start"],
            ["stop"],
            ["restart"],
            ["status"],
            ["doctor"],
            ["smoke"],
            ["test"],
            ["logs"],
            ["cache", "inspect"],
            ["cache", "prune", "--dry-run"],
            ["cache", "prune", "--apply"],
            ["version"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(callable(parser.parse_args(argv).handler))

    def test_main_uses_project_configuration_without_running_shell_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".env").write_text(
                'FUSION_READER_V2_PORT=19015\nFUSION_READER_BIND_HOST="::1"\nFUSION_READER_CHAT_MODEL="$(literal)"\n',
                encoding="utf-8",
            )
            with (
                mock.patch.object(fusionctl, "__file__", str(repo / "scripts/fusionctl.py")),
                mock.patch.dict(fusionctl.os.environ, {"FUSION_READER_V2_PORT": "19016"}, clear=True),
                mock.patch.object(fusionctl, "command_status", return_value=0) as status,
            ):
                self.assertEqual(fusionctl.main(["status"]), 0)
            settings = status.call_args.args[0]
            self.assertEqual(fusionctl._base_url(settings), "http://[::1]:19016")
            self.assertEqual(settings.providers.chat_model, "$(literal)")

    def test_startup_probe_rejects_listener_with_a_different_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            with (
                mock.patch.object(fusionctl.socket, "create_connection"),
                mock.patch.object(fusionctl, "_owned_server_pid", return_value=(123, "owned")),
                mock.patch.object(fusionctl, "_request_json", return_value={"ok": True, "status": "live", "pid": 456}),
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(fusionctl.probe_startup(settings, mock.Mock()), 2)
            self.assertEqual(json.loads(out.getvalue())["error"], "port_occupied")

    def test_test_command_invokes_isolated_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tests").mkdir()
            runner = repo / "scripts/run_isolated_tests.py"
            runner.parent.mkdir()
            runner.touch()
            settings = self._settings(repo)
            with mock.patch.object(
                fusionctl, "run_owned", return_value=mock.Mock(returncode=0, stdout="", stderr="")
            ) as run:
                self.assertEqual(fusionctl.command_test(settings, mock.Mock()), 0)
            self.assertEqual(run.call_args.args[0], [sys.executable, str(runner)])

    def test_cache_inspect_is_read_only_when_root_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = fusionctl.command_cache_inspect(settings, mock.Mock())
            self.assertEqual(code, 0)
            self.assertFalse(settings.paths.cache.exists())
            self.assertEqual(json.loads(output.getvalue())["items"], 0)

    def test_stop_refuses_pid_owner_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            with mock.patch.object(fusionctl, "_owned_server_pid", return_value=(None, "pid_owner_mismatch")):
                with mock.patch.object(fusionctl.os, "kill") as kill, contextlib.redirect_stdout(io.StringIO()):
                    code = fusionctl.command_stop(settings, mock.Mock())
            self.assertEqual(code, 1)
            kill.assert_not_called()

    def test_doctor_does_not_create_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(fusionctl, "_port_open", return_value=False):
                code = fusionctl.command_doctor(settings, mock.Mock())
            self.assertIn(code, (0, 1))
            self.assertFalse(settings.paths.runtime.exists())
            payload = json.loads(output.getvalue())
            self.assertIn("ports", payload)
            self.assertIn("tts_owner", payload)

    def test_source_never_uses_shell_true(self) -> None:
        source = Path(fusionctl.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)

    def test_start_uses_current_python_and_launcher_runs_importable_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "scripts" / "start_fusion_reader_v2.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            settings = self._settings(root)
            with mock.patch.object(
                fusionctl, "run_owned", return_value=mock.Mock(returncode=0, stdout="", stderr="")
            ) as run:
                self.assertEqual(fusionctl.command_start(settings, mock.Mock()), 0)
            self.assertEqual(run.call_args.kwargs["env"]["FUSION_READER_PYTHON"], sys.executable)
            self.assertEqual(run.call_args.kwargs["env"]["FUSION_READER_RUNTIME_ROOT"], str(settings.paths.runtime))
            self.assertEqual(run.call_args.kwargs["env"]["FUSION_READER_RUNTIME_DIR"], str(settings.paths.runtime))
            self.assertEqual(run.call_args.kwargs["env"]["FUSION_READER_LOG_ROOT"], str(settings.paths.logs))

        launcher = Path(fusionctl.__file__).with_name("start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn('"$PYTHON_BIN" -m scripts.fusion_reader_v2_server', launcher)

    def test_launcher_prefers_canonical_roots_and_derives_legacy_aliases(self) -> None:
        launcher = Path(fusionctl.__file__).with_name("start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn("FUSION_READER_RUNTIME_ROOT:-${FUSION_READER_RUNTIME_DIR", launcher)
        self.assertIn("FUSION_READER_LOG_ROOT:-${FUSION_READER_LOG_DIR", launcher)
        self.assertIn('export FUSION_READER_RUNTIME_DIR="$RUNTIME_DIR"', launcher)
        self.assertIn('export FUSION_READER_LOG_DIR="$LOG_DIR"', launcher)


if __name__ == "__main__":
    unittest.main()
