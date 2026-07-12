from __future__ import annotations

import json
import socket
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from fusion_reader_v2 import local_web_bridge
from fusion_reader_v2.openclaw_bridge import ExternalResearchBridge, ExternalResearchResult


class Response:
    def __init__(self, payload: object = None, *, status: int = 200, raw: bytes | None = None) -> None:
        self.status = status
        self.raw = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class StubBridge(ExternalResearchBridge):
    def __init__(self, result: ExternalResearchResult, available: object = True) -> None:
        self.result = result
        self.available_value = available
        self.calls = 0

    def available(self) -> bool:
        if isinstance(self.available_value, Exception):
            raise self.available_value
        return bool(self.available_value)

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        self.calls += 1
        return self.result


class SearxngBridgeMatrixTests(unittest.TestCase):
    def test_available_disabled_success_and_failure(self) -> None:
        self.assertFalse(local_web_bridge.SearxngResearchBridge(enabled=False).available())
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local", timeout_seconds=4)
        with mock.patch.object(local_web_bridge, "urlopen", return_value=Response({}, status=204)):
            self.assertTrue(bridge.available())
        with mock.patch.object(local_web_bridge, "urlopen", side_effect=URLError("down")):
            self.assertFalse(bridge.available())

    def test_research_empty_disabled_no_results_and_sanitized_results(self) -> None:
        self.assertEqual(local_web_bridge.SearxngResearchBridge().research("").detail, "empty_query")
        disabled = local_web_bridge.SearxngResearchBridge(enabled=False).research("tema")
        self.assertEqual(disabled.detail, "searxng_disabled")

        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local/search", max_results=2)
        with mock.patch.object(local_web_bridge, "urlopen", return_value=Response({"results": "bad"})):
            self.assertEqual(bridge.research("vacío").detail, "searxng_no_results")

        results = {
            "results": [
                "invalid",
                {"title": "<b>Fuente uno</b>", "url": "https://example.test/uno", "content": "Nota " * 80},
                {"pretty_url": "https://example.test/dos", "snippet": "Segunda"},
                {"title": "ignored by limit"},
            ]
        }
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local", max_results=3)
        with mock.patch.object(local_web_bridge, "urlopen", return_value=Response(results)):
            result = bridge.research("tema")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.sources), 2)
        self.assertNotIn("<b>", result.answer)
        self.assertNotIn("https://", result.spoken_answer)
        self.assertTrue(result.findings)

    def test_research_external_failure_matrix(self) -> None:
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local")
        cases = (
            (HTTPError("http://local", 500, "error", {}, None), "searxng_http_error"),
            (socket.timeout("slow"), "searxng_timeout"),
            (URLError("down"), "searxng_unavailable"),
        )
        for error, detail in cases:
            with self.subTest(detail=detail), mock.patch.object(local_web_bridge, "urlopen", side_effect=error):
                self.assertEqual(bridge.research("tema").detail, detail)
        with mock.patch.object(local_web_bridge, "urlopen", return_value=Response(raw=b"not-json")):
            self.assertEqual(bridge.research("tema").detail, "searxng_invalid_json")

    def test_formatting_helpers_cover_title_only_empty_and_clipping(self) -> None:
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local")
        sources = bridge._sanitize_sources([{}, {"title": "Sólo título"}, {"content": "nota"}])
        self.assertEqual(len(sources), 2)
        self.assertIn("Sólo título", bridge._build_summary([sources[0]]))
        self.assertEqual(bridge._sanitize_findings([{"title": "Fuente", "note": ""}]), ["Fuente"])
        self.assertIn("Fuente", bridge._format_answer("q", "resumen", [], [{"title": "Fuente"}]))
        self.assertEqual(bridge._format_spoken_answer("", []), "Encontré fuentes en SearXNG local.")
        self.assertTrue(bridge._clip("x" * 30, 10).endswith("..."))


class AutoBridgeMatrixTests(unittest.TestCase):
    def test_auto_selection_fallback_and_availability_edges(self) -> None:
        local_ok = StubBridge(ExternalResearchResult(True, answer="local"), available=True)
        cloud = StubBridge(ExternalResearchResult(True, answer="cloud"), available=True)
        auto = local_web_bridge.AutoExternalResearchBridge(local_ok, cloud)  # type: ignore[arg-type]
        self.assertTrue(auto.available())
        self.assertEqual(auto.research("q").answer, "local")
        self.assertEqual(cloud.calls, 0)

        unavailable = StubBridge(ExternalResearchResult(False, detail="searxng_unavailable"), available=True)
        auto = local_web_bridge.AutoExternalResearchBridge(unavailable, cloud)  # type: ignore[arg-type]
        self.assertEqual(auto.research("q").answer, "cloud")
        unavailable.result = ExternalResearchResult(False, detail="searxng_no_results")
        self.assertEqual(auto.research("q").detail, "searxng_no_results")

        raising = StubBridge(ExternalResearchResult(False), available=RuntimeError("probe"))
        self.assertFalse(auto._bridge_available(raising))

        class NoProbe(ExternalResearchBridge):
            pass

        self.assertTrue(auto._bridge_available(NoProbe()))

    def test_default_provider_selection(self) -> None:
        for provider, expected in (
            ("searxng", local_web_bridge.SearxngResearchBridge),
            ("openclaw", local_web_bridge.OpenClawResearchBridge),
            ("auto", local_web_bridge.AutoExternalResearchBridge),
            ("unknown", local_web_bridge.AutoExternalResearchBridge),
        ):
            with mock.patch.dict("os.environ", {"FUSION_READER_EXTERNAL_RESEARCH_PROVIDER": provider}):
                self.assertIsInstance(local_web_bridge.default_external_research_bridge(), expected)


if __name__ == "__main__":
    unittest.main()
