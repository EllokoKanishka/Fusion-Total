from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
import tempfile
import unittest

from fusion_reader_v2.config import create_settings
from scripts import fusionctl


class STTServerDefaultsTests(unittest.TestCase):
    def test_cli_default_does_not_override_server_quality_default(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "scripts/start_fusion_reader_v2.sh").touch()
            settings = create_settings(environ={}, repository_root=root)
            self.assertEqual(settings.providers.stt_model, "small")
            self.assertEqual(getattr(settings.providers, "stt_server_model", None), "")
            with patch.dict(os.environ, {}, clear=True), patch.object(fusionctl, "run_owned") as run:
                run.return_value.returncode = 0
                fusionctl.command_start(settings, SimpleNamespace(no_engines=False))
            self.assertNotIn("FUSION_READER_STT_MODEL", run.call_args.kwargs["env"])

    def test_explicit_common_or_server_model_is_preserved(self):
        for env in ({"FUSION_READER_STT_MODEL": "chosen-model"}, {"FUSION_READER_STT_SERVER_MODEL": "chosen-model"}):
            settings = create_settings(environ=env)
            self.assertEqual(getattr(settings.providers, "stt_server_model", None), "chosen-model")

    def test_project_interpreter_preferred_without_mutating_global_python(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "scripts/start_fusion_reader_v2.sh").touch()
            python = root / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            settings = create_settings(environ={}, repository_root=root)
            with patch.dict(os.environ, {}, clear=True), patch.object(fusionctl, "run_owned") as run:
                run.return_value.returncode = 0
                fusionctl.command_start(settings, SimpleNamespace(no_engines=False))
            self.assertEqual(run.call_args.kwargs["env"]["FUSION_READER_PYTHON"], str(python))

    def test_legacy_gpu_wait_is_included_in_global_start_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "scripts/start_fusion_reader_v2.sh").touch()
            settings = create_settings(environ={}, repository_root=root)
            with (
                patch.dict(os.environ, {"FUSION_READER_GPU_TTS_WAIT_SECONDS": "450"}, clear=True),
                patch.object(fusionctl, "run_owned") as run,
            ):
                run.return_value.returncode = 0
                fusionctl.command_start(settings, SimpleNamespace(no_engines=False))
            self.assertGreaterEqual(run.call_args.kwargs["timeout"], 450 + 60 + 120 + 40)
