from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fusion_reader_v2 import ConversationCore, ExternalResearchResult, NullChatProvider, NullExternalResearchBridge
from fusion_reader_v2.composition import ProviderBundle, create_fusion_reader, create_http_server, create_providers
from fusion_reader_v2.config import create_settings
from fusion_reader_v2.dialogue import NullSTTProvider
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
