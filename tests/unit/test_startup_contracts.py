from pathlib import Path
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fusion_reader_v2.config import ConfigurationError, create_settings
from fusion_reader_v2.composition import create_providers
from fusion_reader_v2.conversation import OllamaChatProvider
from scripts import fusionctl


class StartupContractsTests(unittest.TestCase):
    def test_nonfinite_settings_and_invalid_ports_are_rejected(self):
        for key, value in [
            ("FUSION_READER_STT_TIMEOUT", "nan"),
            ("FUSION_READER_TTS_TIMEOUT", "inf"),
            ("FUSION_READER_V2_PORT", "65536"),
        ]:
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                create_settings(environ={key: value})

    def test_documented_stt_aliases_are_server(self):
        for alias in ("faster_whisper", "faster-whisper"):
            self.assertEqual(
                create_settings(environ={"FUSION_READER_STT_PROVIDER": alias}).providers.stt_provider, "server"
            )

    def test_cloud_dictation_and_dialogue_share_execution_boundary(self):
        settings = create_settings(environ={"FUSION_READER_OPENAI_EXECUTION_MODE": "infer"})
        bundle = create_providers(settings)
        chat = bundle.conversation.provider.providers["openai"]
        dictation = bundle.dictation_assistant.providers["openai"]
        self.assertEqual(dictation.execution_mode, chat.execution_mode)
        self.assertEqual(dictation.agent_dir, chat.agent_dir)

    def test_empty_catalog_is_not_installed_or_resident(self):
        provider = OllamaChatProvider()
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(b'{"models": []}')):
            health = provider.health()
        self.assertTrue(health["ok"])
        self.assertIs(health["model_present"], False)
        self.assertIs(health["model_resident"], False)

    def test_installed_model_is_distinct_from_resident(self):
        provider = OllamaChatProvider(default_model="local:14b")

        def response(request, **kwargs):
            url = request.full_url if hasattr(request, "full_url") else request
            return io.BytesIO(
                json.dumps({"models": [{"name": "local:14b"}] if url.endswith("/api/tags") else []}).encode()
            )

        with patch("urllib.request.urlopen", side_effect=response):
            health = provider.health()
        self.assertTrue(health["model_present"])
        self.assertFalse(health["model_resident"])

    def test_installer_preserves_unrelated_configuration_and_backup(self):
        from scripts.update_launcher_env import update

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            data = b"# custom\nFUSION_READER_CHAT_MODEL=custom:14b\nFUSION_READER_V2_PORT=8010\n"
            path.write_bytes(data)
            update(path, "/python with spaces", "8123")
            self.assertIn("FUSION_READER_CHAT_MODEL=custom:14b", path.read_text())
            self.assertEqual(list(Path(folder).glob(".env.backup.*"))[0].read_bytes(), data)
            self.assertEqual(path.read_text().count("FUSION_READER_V2_PORT="), 1)

    def test_failed_backup_preserves_configuration(self):
        from scripts.update_launcher_env import update

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_bytes(b"CUSTOM=original\n")
            original = Path.open

            def opened(target, mode="r", *args, **kwargs):
                if mode == "xb":
                    raise OSError("backup unavailable")
                return original(target, mode, *args, **kwargs)

            with patch.object(Path, "open", opened), self.assertRaises(OSError):
                update(path, "python", "8123")
            self.assertEqual(path.read_bytes(), b"CUSTOM=original\n")

    def test_cli_propagates_effective_port_and_sufficient_startup_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "scripts/start_fusion_reader_v2.sh").touch()
            settings = create_settings(
                environ={"FUSION_READER_V2_PORT": "8123", "FUSION_READER_STT_PROVIDER": "none"}, repository_root=root
            )
            with patch.object(fusionctl, "run_owned") as run:
                run.return_value.configure_mock(returncode=0, stdout="", stderr="")
                fusionctl.command_start(settings, None)
            options = run.call_args.kwargs
            self.assertEqual(options["env"]["FUSION_READER_V2_PORT"], "8123")
            self.assertEqual(options["env"]["FUSION_READER_STT_PROVIDER"], "none")
            self.assertGreater(options["timeout"], 90 + 60 + 120 + 40)

    def test_research_inherits_node_path(self):
        settings = create_settings(environ={})
        with patch.dict(os.environ, {"PATH": "/system/bin:/node/bin"}):
            bundle = create_providers(settings)
            bridge = bundle.research.openclaw
            self.assertIsNone(bridge.environment)

    def test_stt_optional_startup_policy_runs_without_blocking_reader(self):
        import subprocess

        script = Path(__file__).resolve().parents[2] / "scripts/start_fusion_reader_v2.sh"
        source = script.read_text()
        block = source.split("# The default STT provider", 1)[1].split("current_commit=", 1)[0]
        block = block[block.index("case ") :]
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / "gpu-policy-fixture.sh"
            fixture.write_text(
                'set -eu\nensure_fusion_gpu_stt() { echo called; return 1; }\nlog_msg() { echo "$*"; }\n'
                + block
                + "\necho reader-continues\n"
            )
            for provider in ("none", "cli", "auto", "server"):
                result = subprocess.run(
                    ["/bin/bash", str(fixture)],
                    env={**os.environ, "FUSION_READER_STT_PROVIDER": provider},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("reader-continues", result.stdout)
                self.assertEqual("called" in result.stdout, provider in ("auto", "server"))

    def test_evicted_model_is_preloaded_again_despite_cached_ready(self):
        from unittest.mock import Mock
        from fusion_reader_v2.conversation import NullChatProvider
        from fusion_reader_v2.dictation_assistant import DictationAssistant
        from fusion_reader_v2.web.context import WebContext
        from tests.helpers import managed_test_app

        provider = NullChatProvider("{}")
        provider.default_model = "local:14b"
        provider.health = Mock(return_value={"ok": True, "model_present": True, "model_resident": True})
        provider.preload_model = Mock(return_value={"ok": True, "model": "local:14b"})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = create_settings(environ={"HOME": folder}, repository_root=root)
            with managed_test_app(
                root=root, dictation_assistant=DictationAssistant({"local": provider}, selected="local")
            ) as app:
                context = WebContext(app=app, settings=settings, runtime_info={})
                self.assertEqual(context.warm_dictation_model()["state"], "ready")
                provider.health.return_value["model_resident"] = False
                self.assertEqual(context.dictation_model_warm_status()["state"], "cold")
                context.warm_dictation_model()
                self.assertEqual(provider.preload_model.call_count, 2)


if __name__ == "__main__":
    unittest.main()
