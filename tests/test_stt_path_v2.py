import os
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2.dialogue import (
    AutoSTTProvider,
    FasterWhisperServerSTTProvider,
    WhisperCliSTTProvider,
    default_stt_provider,
)


class SttPathV2Tests(unittest.TestCase):
    def provider(self, value=None):
        env = {} if value is None else {"FUSION_READER_STT_PROVIDER": value}
        with mock.patch.dict(os.environ, env, clear=True):
            return default_stt_provider()

    def test_provider_modes_and_aliases(self):
        self.assertIsInstance(self.provider(), AutoSTTProvider)
        self.assertIsInstance(self.provider("auto"), AutoSTTProvider)
        self.assertIsInstance(self.provider("cli"), WhisperCliSTTProvider)
        for alias in ("server", "faster_whisper", "faster-whisper"):
            self.assertIsInstance(self.provider(alias), FasterWhisperServerSTTProvider)

    def test_invalid_provider_keeps_legacy_auto_behavior(self):
        provider = self.provider("unknown")
        self.assertIsInstance(provider, AutoSTTProvider)
        self.assertEqual(provider.requested_provider, "auto")

    def test_cli_command_override_is_respected(self):
        with mock.patch.dict(os.environ, {"FUSION_READER_STT_COMMAND": "/tmp/custom-whisper"}, clear=True):
            self.assertEqual(WhisperCliSTTProvider().command, "/tmp/custom-whisper")

    def test_launchers_and_env_resolution_are_structurally_consistent(self):
        launcher = Path("scripts/open_fusion_reader.sh").read_text(encoding="utf-8")
        self.assertIn('[[ "$STT_PROVIDER" == "cli" ]]', launcher)
        self.assertIn("inicio omitido porque el provider es cli", launcher)
        self.assertIn("intentando iniciar para provider", launcher)
        server = Path("scripts/start_fusion_reader_v2_stt.sh").read_text(encoding="utf-8")
        self.assertIn("FUSION_READER_STT_ENV:-${FUSION_READER_GPU_ENV:-", server)
        self.assertIn("Set FUSION_READER_STT_ENV to a valid STT environment", server)
        self.assertIn("FUSION_READER_GPU_ENV remains a compatible fallback", server)
        self.assertIn("For the historical default only", server)

    def test_smoke_classifies_cli_without_server_as_info(self):
        smoke = Path("scripts/smoke_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn("is not listening and is not required in cli mode", smoke)
        self.assertIn('warn "STT ${STT_PORT} is not listening for provider ${STT_PROVIDER}"', smoke)


if __name__ == "__main__":
    unittest.main()
