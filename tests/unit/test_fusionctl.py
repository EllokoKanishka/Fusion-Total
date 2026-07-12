from __future__ import annotations

import contextlib
import io
import json
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
            with contextlib.redirect_stdout(output):
                code = fusionctl.command_doctor(settings, mock.Mock())
            self.assertIn(code, (0, 1))
            self.assertFalse(settings.paths.runtime.exists())
            payload = json.loads(output.getvalue())
            self.assertIn("ports", payload)
            self.assertIn("tts_owner", payload)

    def test_source_never_uses_shell_true(self) -> None:
        source = Path(fusionctl.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
