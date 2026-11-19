from __future__ import annotations

import unittest

from fusion_reader_v2.openclaw_bridge import ExternalResearchResult, NullExternalResearchBridge
from fusion_reader_v2.services.research import ResearchService


class ResearchServiceTests(unittest.TestCase):
    def test_research_sends_only_the_explicit_query(self) -> None:
        bridge = NullExternalResearchBridge(ExternalResearchResult(True, answer="resultado", provider="null"))
        service = ResearchService(bridge)
        self.assertTrue(service.is_explicit_request("busca en internet tesis sobre lectura"))
        self.assertFalse(service.is_explicit_request("explicá este párrafo"))
        self.assertFalse(service.is_explicit_request("Buscá fuentes, pero no busques en internet"))
        result = service.research("busca fuentes")
        self.assertTrue(result.ok)
        self.assertEqual(bridge.calls[0][1], {})

    def test_health_degrades_without_probe(self) -> None:
        service = ResearchService(NullExternalResearchBridge())
        health = service.health()
        self.assertFalse(health["ok"])
        self.assertEqual(health["detail"], "bridge_has_no_available_probe")


if __name__ == "__main__":
    unittest.main()
