from __future__ import annotations

import json
import socket
import unittest
from datetime import datetime, timedelta
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from fusion_reader_v2 import local_web_bridge
from fusion_reader_v2.openclaw_bridge import ExternalResearchBridge, ExternalResearchResult
from fusion_reader_v2.services.research import ResearchService


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
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local/search", timeout_seconds=4)
        with mock.patch.object(local_web_bridge, "urlopen", return_value=Response({"engines": []})) as open_url:
            self.assertTrue(bridge.available())
            self.assertEqual(open_url.call_args.args[0].full_url, "http://local/config")
        with mock.patch.object(local_web_bridge, "urlopen", return_value=Response(raw=b"<html>Open WebUI</html>")):
            self.assertFalse(bridge.available())
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

    def test_news_for_today_prioritizes_publication_dates_and_marks_undated_results(self) -> None:
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local", max_results=2)
        now = datetime.now().astimezone()
        old = {
            "title": "Ayer",
            "url": "https://example.test/ayer",
            "publishedDate": (now - timedelta(days=1)).isoformat(),
        }
        undated = {"title": "Fecha pendiente", "url": "https://example.test/sin-fecha"}
        current = {"title": "Hoy", "url": "https://example.test/hoy", "publishedDate": now.isoformat()}
        with mock.patch.object(local_web_bridge, "urlopen") as open_url:
            open_url.return_value = Response({"results": [old, undated, current]})
            result = bridge.research_news("geopolítica", today=True)
            params = parse_qs(urlsplit(open_url.call_args.args[0].full_url).query)
            self.assertEqual(params["categories"], ["news"])
            self.assertEqual(params["time_range"], ["day"])
            self.assertEqual(params["q"], ["geopolítica"])
            self.assertEqual([item["url"] for item in result.sources], [current["url"]])
            self.assertEqual(result.sources[0]["published_date"], now.date().isoformat())
            open_url.return_value = Response({"results": [old, undated]})
            unconfirmed = bridge.research_news("geopolítica", today=True)
            self.assertEqual(unconfirmed.detail, "external_research_news_unconfirmed")
            self.assertEqual([item["url"] for item in unconfirmed.sources], [undated["url"]])
            self.assertIn("sin fecha verificable", unconfirmed.answer)
            self.assertIn("sin fecha verificable", unconfirmed.spoken_answer)

    def test_daily_news_retries_without_the_engine_date_filter(self) -> None:
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local")
        now = datetime.now().astimezone()
        recent = {
            "title": "Fecha confirmada",
            "url": "https://example.test/hoy",
            "publishedDate": now.isoformat(),
        }
        earlier = {**recent, "url": "https://example.test/ayer", "publishedDate": (now - timedelta(days=1)).isoformat()}
        unknown = {"title": "Fecha desconocida", "url": "https://example.test/incierto"}
        with mock.patch.object(
            local_web_bridge,
            "urlopen",
            side_effect=[Response({"results": []}), Response({"results": [earlier, unknown, recent]})],
        ) as opening:
            result = bridge.research_news("geopolítica", today=True)
        first, second = [parse_qs(urlsplit(call.args[0].full_url).query) for call in opening.call_args_list]
        self.assertEqual(first["time_range"], ["day"])
        self.assertNotIn("time_range", second)
        self.assertEqual([source["url"] for source in result.sources], [recent["url"]])

    def test_daily_news_presents_only_reviewed_provider_results(self) -> None:
        provider = local_web_bridge.SearxngResearchBridge()
        today = datetime.now().astimezone().date().isoformat()
        original = {"title": "Coincidencia tangencial", "url": "https://example.test/ruido", "published_date": today}
        relevant = {"title": "Una negociación concreta", "url": "https://example.test/nota", "published_date": today}
        review = mock.Mock(return_value='{"relevant":[2,2,99]}')
        with mock.patch.object(
            provider,
            "research_news",
            side_effect=[
                ExternalResearchResult(True, provider="searxng", sources=[original]),
                ExternalResearchResult(True, provider="searxng", sources=[relevant]),
            ],
        ) as search:
            result = ResearchService(provider).research(
                "Buscá las noticias de geopolítica de hoy.",
                news_queries=lambda _topic: '{"queries":["política internacional"]}',
                review_news_sources=review,
            )
        self.assertEqual(search.call_args_list, [
            mock.call("geopolítica", today=True), mock.call("política internacional", today=True),
        ])
        self.assertEqual(result.query, "geopolítica | política internacional")
        self.assertEqual([source["url"] for source in result.sources], [relevant["url"]])
        self.assertNotIn(original["title"], result.answer)
        self.assertIn(relevant["title"], result.spoken_answer)

    def test_scientific_search_has_a_bounded_engine_deadline_and_bibliographic_fallback(self) -> None:
        bridge = local_web_bridge.SearxngResearchBridge(base_url="http://local", timeout_seconds=12)
        article = {"results": [{"title": "Educación e IA", "url": "https://doi.org/10.1234/example"}]}
        with mock.patch.object(
            local_web_bridge, "urlopen", side_effect=[Response({"results": []}), Response(article)]
        ) as opening:
            self.assertTrue(bridge.research_academic("educación IA 2026").ok)
        first, second = [parse_qs(urlsplit(call.args[0].full_url).query) for call in opening.call_args_list]
        self.assertEqual(first["categories"], ["scientific publications"])
        self.assertLess(float(first["timeout_limit"][0]), 12)
        self.assertEqual(second["engines"], ["crossref,semantic scholar"])
        self.assertEqual(first["q"], second["q"])

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
