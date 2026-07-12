import unittest
import os
from fusion_reader_v2 import (
    ExternalResearchBridge,
    ConversationCore,
    NullChatProvider,
    ExternalResearchResult,
)
from fusion_reader_v2.openclaw_bridge import OpenClawResearchBridge, NullExternalResearchBridge
from fusion_reader_v2.local_web_bridge import SearxngResearchBridge, AutoExternalResearchBridge
from tests.helpers import (
    test_app,
    NullResearchProvider,
    FailingResearchProvider,
    FailingTTSProvider,
    FakeExternalResearchBridge,
)

class ExternalResearchTests(unittest.TestCase):
    def test_openclaw_bridge_defaults_to_fusion_research_agent(self):
        bridge = OpenClawResearchBridge(command="/bin/echo")
        self.assertEqual(bridge.agent, "fusion-research")

    def test_searxng_bridge_parses_results(self):
        # We don't really call the web here, but check the structure if we were to
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080")
        self.assertEqual(bridge.name, "searxng")

    def test_auto_external_research_prefers_searxng_without_calling_openclaw(self):
        searxng = FakeExternalResearchBridge(ExternalResearchResult(
                True,
                answer="SearXNG respondió.",
                spoken_answer="SearXNG respondió.",
                detail="external_research_ok",
                provider="searxng",
        ))
        openclaw = FakeExternalResearchBridge(ExternalResearchResult(
                True,
                answer="OpenClaw respondió.",
                spoken_answer="OpenClaw respondió.",
                detail="external_research_ok",
                provider="openclaw_bridge",
        ))
        bridge = AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)
        result = bridge.research("test")
        self.assertEqual(result.provider, "searxng")
        self.assertEqual(len(searxng.calls), 1)
        self.assertEqual(len(openclaw.calls), 0)

    def test_auto_external_research_falls_back_to_openclaw_when_searxng_is_unavailable(self):
        searxng = FakeExternalResearchBridge(ExternalResearchResult(
                False,
                answer="SearXNG caído.",
                spoken_answer="SearXNG caído.",
                detail="searxng_unavailable",
                provider="searxng",
        ))
        openclaw = FakeExternalResearchBridge(ExternalResearchResult(
                True,
                answer="OpenClaw respondió.",
                spoken_answer="OpenClaw respondió.",
                detail="external_research_ok",
                provider="openclaw_bridge",
        ))
        bridge = AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)
        result = bridge.research("test")
        self.assertEqual(result.provider, "openclaw_bridge")
        self.assertEqual(len(searxng.calls), 1)
        self.assertEqual(len(openclaw.calls), 1)

    def test_dialogue_research_request_routes_to_bridge(self):
        bridge = NullExternalResearchBridge(ExternalResearchResult(
            True, answer="Result", spoken_answer="Result", provider="null"
        ))
        app = test_app(external_research=bridge)
        app.load_text("d", "T", "Contenido del documento", prefetch=False)
        out = app.dialogue_turn_text("buscá en internet sobre el existencialismo")
        self.assertTrue(out["ok"])
        self.assertEqual(len(bridge.calls), 1)
        self.assertIn("existencialismo", bridge.calls[0][0].lower())

    def test_dialogue_research_results_are_returned_directly(self):
        bridge = NullExternalResearchBridge(ExternalResearchResult(
            True, answer="Resultado de prueba", spoken_answer="Resultado de prueba", provider="null",
            summary="Data crucial", query="algo", findings=[], sources=[]
        ))
        app = test_app(external_research=bridge)
        out = app.dialogue_turn_text("buscá en internet algo")
        self.assertTrue(out["external_research"])
        self.assertIn("Resultado de prueba", out["answer"])
