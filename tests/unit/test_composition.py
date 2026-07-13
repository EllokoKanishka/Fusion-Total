from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fusion_reader_v2 import ConversationCore, ExternalResearchResult, NullChatProvider, NullExternalResearchBridge
from fusion_reader_v2.composition import ProviderBundle, create_fusion_reader, create_http_server, create_providers
from fusion_reader_v2.config import create_settings
from fusion_reader_v2.dialogue import NullSTTProvider
from fusion_reader_v2.dialogue import AutoSTTProvider, FasterWhisperServerSTTProvider, WhisperCliSTTProvider
from fusion_reader_v2.local_web_bridge import AutoExternalResearchBridge, SearxngResearchBridge
from fusion_reader_v2.openclaw_bridge import OpenClawResearchBridge
from tests.helpers import SyntheticWavTTSProvider


class CompositionTests(unittest.TestCase):
    def _settings(self, root: Path):
        settings = create_settings(
            repository_root=Path.cwd(),
            environ={
                "HOME": str(root),
                "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
            },
        )
        return replace(settings, ports=replace(settings.ports, api=0))

    def test_default_provider_construction_is_side_effect_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            bundle = create_providers(settings)
            self.assertEqual(bundle.tts.default_voice, settings.providers.voice)
            self.assertIsNotNone(bundle.stt)
            self.assertIsNotNone(bundle.conversation)
            self.assertIsNotNone(bundle.research)
            self.assertFalse(settings.paths.runtime.exists())

    def test_settings_none_build_disabled_null_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = create_settings(
                repository_root=Path.cwd(),
                environ={
                    "HOME": tmp,
                    "FUSION_READER_STT_PROVIDER": "none",
                    "FUSION_READER_EXTERNAL_RESEARCH_PROVIDER": "none",
                },
            )
            bundle = create_providers(settings)
            self.assertIsInstance(bundle.stt, NullSTTProvider)
            self.assertFalse(bundle.stt.health()["ok"])
            self.assertIsInstance(bundle.research, NullExternalResearchBridge)
            self.assertEqual(bundle.research.research("x").detail, "bridge_disabled")

    def test_settings_build_exact_stt_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = {
                "HOME": tmp,
                "FUSION_READER_STT_URL": "http://127.0.0.1:19021",
                "FUSION_READER_STT_TIMEOUT": "7.25",
                "FUSION_READER_STT_COMMAND": "/opt/fusion/whisper",
                "FUSION_READER_STT_MODEL": "tiny",
                "FUSION_READER_STT_THREADS": "3",
            }
            server = create_providers(create_settings(environ={**base, "FUSION_READER_STT_PROVIDER": "server"})).stt
            self.assertIsInstance(server, FasterWhisperServerSTTProvider)
            self.assertEqual((server.base_url, server.timeout_seconds), (base["FUSION_READER_STT_URL"], 7.25))
            cli = create_providers(create_settings(environ={**base, "FUSION_READER_STT_PROVIDER": "cli"})).stt
            self.assertIsInstance(cli, WhisperCliSTTProvider)
            self.assertEqual((cli.command, cli.model, cli.timeout_seconds, cli.threads), ("/opt/fusion/whisper", "tiny", 7.25, 3))
            auto = create_providers(create_settings(environ={**base, "FUSION_READER_STT_PROVIDER": "auto"})).stt
            self.assertIsInstance(auto, AutoSTTProvider)
            self.assertEqual(auto.primary.base_url, base["FUSION_READER_STT_URL"])
            self.assertEqual(auto.fallback.command, "/opt/fusion/whisper")

    def test_settings_build_exact_research_modes_and_never_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = {
                "HOME": tmp,
                "FUSION_READER_SEARXNG_URL": "http://127.0.0.1:18080",
                "FUSION_READER_OPENCLAW_BIN": "/opt/fusion/openclaw",
            }
            searxng = create_providers(
                create_settings(environ={**base, "FUSION_READER_EXTERNAL_RESEARCH_PROVIDER": "searxng"})
            ).research
            self.assertIsInstance(searxng, SearxngResearchBridge)
            self.assertEqual(searxng.base_url, base["FUSION_READER_SEARXNG_URL"])
            openclaw = create_providers(
                create_settings(environ={**base, "FUSION_READER_EXTERNAL_RESEARCH_PROVIDER": "openclaw"})
            ).research
            self.assertIsInstance(openclaw, OpenClawResearchBridge)
            self.assertEqual(openclaw.agent, "fusion-research")
            auto = create_providers(
                create_settings(environ={**base, "FUSION_READER_EXTERNAL_RESEARCH_PROVIDER": "auto"})
            ).research
            self.assertIsInstance(auto, AutoExternalResearchBridge)
            self.assertEqual(auto.openclaw.agent, "fusion-research")

    def test_provider_bundles_are_deterministic_and_independent_from_environment(self) -> None:
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            first_settings = create_settings(
                environ={"HOME": tmp, "FUSION_READER_STT_PROVIDER": "cli", "FUSION_READER_STT_COMMAND": "first"}
            )
            second_settings = create_settings(
                environ={"HOME": tmp, "FUSION_READER_STT_PROVIDER": "cli", "FUSION_READER_STT_COMMAND": "second"}
            )
            with mock.patch.dict(os.environ, {"FUSION_READER_STT_COMMAND": "mutated"}, clear=False):
                first = create_providers(first_settings)
                second = create_providers(second_settings)
            self.assertEqual(first.stt.command, "first")
            self.assertEqual(second.stt.command, "second")
            self.assertIsNot(first.stt, second.stt)

    def test_injected_composition_builds_and_closes_app_and_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(root)
            bundle = ProviderBundle(
                tts=SyntheticWavTTSProvider(output_root=root / "tts"),
                stt=NullSTTProvider(),
                conversation=ConversationCore(NullChatProvider("ok")),
                research=NullExternalResearchBridge(ExternalResearchResult(False, detail="unused")),
            )
            app = create_fusion_reader(settings, bundle)
            server = create_http_server(app, settings)
            try:
                self.assertEqual(server.server_address[1] > 0, True)
                self.assertEqual(app.status()["doc_id"], "")
            finally:
                server.server_close()
                app.shutdown_background_work(timeout=10.0)


if __name__ == "__main__":
    unittest.main()
