from __future__ import annotations

import base64
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fusion_reader_v2 import DictationAssistant, NullChatProvider
from fusion_reader_v2.config import ConfigurationError, SecuritySettings, create_settings
from fusion_reader_v2.conversation import SelectableChatProvider
from fusion_reader_v2.dialogue import NullSTTProvider
from fusion_reader_v2.openclaw_bridge import ExternalResearchResult, NullExternalResearchBridge
from fusion_reader_v2.pdf_to_docx import ConversionResult, JobStatus
from fusion_reader_v2.web import server as web_server
from fusion_reader_v2.web.jobs import register_pdf_to_docx_download
from fusion_reader_v2.web.routes import tools as tools_routes
from tests.helpers import SyntheticWavTTSProvider, make_reading_sections, managed_test_app


class WebServerIntegrationTests(unittest.TestCase):
    def test_disconnected_laboratory_request_does_not_publish_late_answer(self) -> None:
        from http.client import HTTPConnection
        from fusion_reader_v2.laboratory_turn import LaboratoryTurnCancelled

        entered = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        session = str(uuid4())
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            app = server.context.app
            archive = app.archive_laboratory_turn

            def delayed(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(4))
                return {"ok": True, "answer": "Respuesta tardía", "model": "test"}

            def publish(*args, **kwargs):
                try:
                    return archive(*args, **kwargs)
                except LaboratoryTurnCancelled:
                    cancelled.set()
                    raise

            client = HTTPConnection("127.0.0.1", server.server_port)
            with patch.object(app, "chat", side_effect=delayed), patch.object(
                app, "archive_laboratory_turn", side_effect=publish
            ):
                try:
                    client.request(
                        "POST", "/api/chat",
                        json.dumps({"message": "Pregunta", "laboratory_session": session}),
                        {"Content-Type": "application/json"},
                    )
                    self.assertTrue(entered.wait(3))
                    client.close()
                    release.set()
                    self.assertTrue(cancelled.wait(3))
                    self.assertEqual(app.archived_laboratory_turns(session)["turns"], [])
                finally:
                    release.set()
                    client.close()

    def test_laboratory_preserves_search_failure_and_executes_an_indirect_portal_request(self) -> None:
        from urllib.parse import urlencode

        from fusion_reader_v2.services.laboratory_archive import ArchiveScope, LaboratoryArchive
        from fusion_reader_v2.services.research import WebIntent

        initial = "¿Podés buscar las principales noticias sobre geopolítica de hoy?"
        failure = "Busqué noticias de hoy sobre geopolítica. Los resultados con fecha no eran suficientemente pertinentes."
        portal = (
            "Ok, pero la pregunta es esta. ¿Vos podés acceder a la red para entrar a algún portal de noticias "
            "y fijarte en la parte de geopolítica cuáles son los principales títulos?"
        )
        grant = "Bueno, yo te habilito a hacer una búsqueda externa."
        report = {"title": "Titular de la sección", "url": "https://news.un.org/es/story/2026/11/1542201"}
        stt = NullSTTProvider(portal)
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            app = server.context.app
            app.set_laboratory_mode("free")
            model = NullChatProvider("Pertinentes al pedido de geopolítica.")
            app.conversation.provider = model
            app.conversation.classify_laboratory_web_request = classify = Mock(
                return_value='{"operation":"headlines","topic":"geopolítica","today":false}'
            )
            app._research_service.bridge = NullExternalResearchBridge(
                ExternalResearchResult(
                    False, answer=failure, detail="external_research_news_no_relevant_results", provider="searxng"
                )
            )
            port = server.server_port
            base = f"http://127.0.0.1:{port}"
            session = str(uuid4())
            turn = {"laboratory_session": session, "provider": "openai"}

            def chat(text: str, allowed: bool = True) -> dict:
                return self._request(
                    base, "/api/chat", {**turn, "message": text, "allow_external_research": allowed}
                )[1]

            def speak(allowed: bool) -> dict:
                request = urllib.request.Request(
                    base + "/api/dialogue/turn?" + urlencode({**turn, "allow_external_research": int(allowed)}),
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.load(response)

            self.assertEqual(chat(initial)["answer"], failure)
            self.assertIn("geopolítica", chat("¿Pertinentes a qué?")["answer"])
            transcript = [m["content"] for m in model.calls[-1][0]]
            self.assertIn(initial, transcript)
            self.assertIn(failure, transcript)

            response = ExternalResearchResult(
                True, answer="Encontré un titular en Noticias ONU.", detail="external_research_headlines",
                provider="un_news", query="geopolítica", sources=[report],
            )
            with patch("fusion_reader_v2.facade.public_international_headlines", return_value=response) as headline:
                self.assertEqual(speak(False)["detail"], "external_research_permission_required")
                headline.assert_not_called()
                archive = LaboratoryArchive(app.session_state_path.with_suffix(".laboratory.sqlite3"))
                scope = ArchiveScope(session, "openai", "", "free")
                self.assertEqual(archive.restore(scope)[1].pending.web_intent, WebIntent("headlines", "geopolítica", True))
                app._dialogue_service.clear_laboratory_session(session)
                stt.text = grant
                resumed = speak(True)
                self.assertEqual(resumed["external_provider"], "un_news")
                self.assertEqual(resumed["external_sources"][0]["url"], report["url"])
                headline.assert_called_once_with("geopolítica", today=True)
                self.assertEqual(chat(grant)["detail"], "external_research_links")
                headline.assert_called_once()
                self.assertEqual(classify.call_count, 1)
                chat("Te leo un ejemplo. ¿Podés entrar a la red para ver los titulares?")
                self.assertEqual(classify.call_count, 1)

    def test_public_source_is_read_saved_and_archived_in_its_own_thread(self) -> None:
        from hashlib import sha256
        from urllib.parse import urlencode

        from fusion_reader_v2.services.laboratory_archive import ArchiveScope, LaboratoryArchive
        from fusion_reader_v2.services.source_reader import OpenedSource

        paper = "https://publisher.example/elaboracion"
        pdf = "https://publisher.example/elaboracion.pdf"
        body = "[Pagina 1] La lectura crítica requiere distinguir una deducción de sus premisas."
        raw = b"%PDF-1.4 publisher bytes"
        found = [
            {"title": "Otro artículo", "url": "https://publisher.example/otro"},
            {"title": "Elaboración crítica", "url": paper},
        ]
        retrieval = OpenedSource(
            paper, pdf, "Elaboración crítica", body, "pdf", pdf=raw, digest=sha256(raw).hexdigest()
        )
        stt = NullSTTProvider("Descargalo y agregalo como documento de apoyo.")
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            app = server.context.app
            app.load_text("prueba", "Principal", "Un texto de prueba.", prefetch=False)
            app._research_service.bridge = NullExternalResearchBridge(
                ExternalResearchResult(True, provider="searxng", sources=found)
            )
            base = f"http://127.0.0.1:{server.server_port}"
            session = str(uuid4())
            turn = {
                "laboratory_session": session,
                "document_generation": app._document_generation,
                "chunk_index": 0,
            }

            def chat(message: str, *, allowed: bool = True, **overrides) -> dict:
                return self._request(
                    base,
                    "/api/chat",
                    {**turn, "message": message, "allow_external_research": allowed, **overrides},
                )[1]

            with patch("fusion_reader_v2.services.source_reader.read_source", return_value=retrieval) as fetch:
                self.assertEqual(
                    chat("Abrí el segundo enlace.", allowed=False)["detail"], "external_research_permission_required"
                )
                fetch.assert_not_called()
                opened = chat("Buscá un paper sobre lectura crítica y abrí el segundo resultado.")
                self.assertEqual(opened["detail"], "external_source_opened")
                self.assertEqual(opened["external_sources"][0]["url"], pdf)
                self.assertEqual(fetch.call_args.args[0]["url"], paper)
                self.assertEqual(app.session.document.title, "Principal")
                self.assertEqual(app.status()["reference_documents"], [])

                request = urllib.request.Request(
                    base + "/api/dialogue/turn?" + urlencode({**turn, "allow_external_research": 1}),
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.load(response)
                self.assertEqual(saved["detail"], "external_source_saved")
                path = app.audio_export_root / "Elaboración crítica.pdf"
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(len(app.status()["reference_documents"]), 1)
                self.assertEqual(app.session.document.title, "Principal")
                self.assertEqual(fetch.call_count, 1)

            other = str(uuid4())
            other_reply = chat("Abrí el segundo enlace.", laboratory_session=other)
            self.assertEqual(other_reply["detail"], "external_source_required")
            archive = LaboratoryArchive(app.session_state_path.with_suffix(".laboratory.sqlite3"))
            self.assertEqual(len(archive.turns(session)), 3)
            self.assertEqual(len(archive.turns(other)), 1)
            scope = ArchiveScope(session, "local", "prueba", app.laboratory_mode)
            _, research = archive.restore(scope)
            self.assertEqual(research.opened_digest, sha256(raw).hexdigest())
            self.assertIn(body, research.opened_context)
            archive.remove(other)
            archive.record(ArchiveScope(other, "local", "", "free"), "Tarde", "Un turno demorado", [], None, None)
            self.assertEqual(archive.turns(other), [])
            self.assertEqual(len(archive.turns(session)), 3)

    def test_laboratory_research_plans_topics_and_resumes_the_approved_request(self) -> None:
        from urllib.parse import urlencode

        passage = (
            "La soberanía cognitiva requiere preservar la autonomía del juicio. "
            "La educación debe discutir qué delegamos a la inteligencia artificial."
        )
        query = "soberanía cognitiva autonomía juicio educación"
        url = "https://example.org/articles/soberania.pdf?source=" + "x" * 560
        sources = [
            {"title": "Soberanía cognitiva y autonomía del juicio", "url": url, "note": "Una tesis sobre educación."},
            {
                "title": "Juicio profesional y educación superior",
                "url": "https://example.org/articles/evaluacion.pdf",
                "note": "Sobre las limitaciones de la educación.",
            },
            {
                "title": "Real Decreto sobre el Documento Nacional",
                "url": "https://www.boe.es/buscar/",
                "note": "Bloque 1 del documento.",
            },
        ]
        opening = "¿Podrías buscar información en la red con respecto a lo que dice en el bloque 1 del documento?"
        correction = (
            "Sí, yo lo que pedí es lo siguiente. El texto habla sobre varios temas. "
            "Lo que yo quería es que interpretes sobre qué temas está hablando y busques información académica "
            "sobre esos temas que vos identificás."
        )
        stt = NullSTTProvider(opening)
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            app = server.context.app
            model = NullChatProvider(json.dumps({"topics": [{"topic": "Soberanía cognitiva", "query": query}]}))
            bridge = NullExternalResearchBridge(ExternalResearchResult(True, sources=sources))
            app.conversation.provider = model
            app.conversation.review_research_sources = review = Mock(return_value='{"relevant":[1]}')
            app._research_service.bridge = bridge
            app.load_text("prueba", "expo final.pdf", passage, prefetch=False)
            app.set_reasoning_mode("normal")
            base = f"http://127.0.0.1:{server.server_port}"
            turn = {
                "laboratory_session": str(uuid4()),
                "document_generation": app._document_generation,
                "chunk_index": 0,
            }

            def audio(allowed: bool) -> dict:
                request = urllib.request.Request(
                    base + "/api/dialogue/turn?" + urlencode({**turn, "allow_external_research": int(allowed)}),
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=6) as response:
                    return json.load(response)

            out = audio(True)
            self.assertEqual(out["external_query"], query)
            self.assertEqual(bridge.calls, [(query, {})])
            self.assertIn(passage, model.calls[-1][0][-1]["content"])
            self.assertEqual(review.call_args.args[0][1]["title"], "Juicio profesional y educación superior")
            self.assertEqual([item["url"] for item in out["external_sources"]], [url])
            self.assertNotIn("BOE", out["answer"])
            self.assertNotIn(url, out["spoken_answer"])

            stt.text = "Ok, pero ¿me podrías dejar acá los links como para que yo entre directamente?"
            links = audio(False)
            self.assertEqual(links["external_sources"][0]["url"], url)
            self.assertIn(url, links["answer"])
            self.assertEqual(len(bridge.calls), 1)

            stt.text = correction
            self.assertEqual(audio(False)["detail"], "external_research_permission_required")
            self.assertEqual(len(bridge.calls), 1)
            stt.text = "Bueno, ¿podés realizarla?"
            self.assertEqual(audio(False)["detail"], "external_research_permission_required")
            _, other = self._request(
                base,
                "/api/chat",
                {
                    **turn,
                    "laboratory_session": str(uuid4()),
                    "message": "Podés realizarla?",
                    "allow_external_research": True,
                },
            )
            self.assertFalse(other.get("external_research", False))
            self.assertEqual(len(bridge.calls), 1)
            stt.text = "A ver, ahí te habilité la búsqueda en internet, fíjate."
            self.assertEqual(audio(True)["external_query"], query)
            self.assertEqual(bridge.calls, [(query, {}), (query, {})])

            stt.text = correction
            self.assertEqual(audio(False)["detail"], "external_research_permission_required")
            self._request(base, "/api/chat", {**turn, "message": "No busques en internet."})
            stt.text = "Bueno, ¿podés realizarla?"
            self.assertFalse(audio(True).get("external_research", False))
            self.assertEqual(len(bridge.calls), 2)

            stt.text = "Podés separar los temas de este bloque y buscar bibliografía en el documento."
            self.assertFalse(audio(True).get("external_research", False))
            self.assertEqual(len(bridge.calls), 2)

            stt.text = (
                "A ver, ¿podés buscar, quiero decir, separar los temas que trata este bloque y buscar alguna, algún..."
            )
            self.assertEqual(audio(True)["detail"], "external_research_confirmation")
            self.assertEqual(len(bridge.calls), 2)
            stt.text = "Ok, dejé prendida la búsqueda para más tarde."
            self.assertFalse(audio(True).get("external_research", False))
            self.assertEqual(len(bridge.calls), 2)
            stt.text = "Ok, dejé prendida la búsqueda, así que podés buscar."
            self.assertEqual(audio(True)["external_query"], query)
            self.assertEqual(len(bridge.calls), 3)

            review.return_value = "No pude evaluar las fuentes."
            stt.text = opening
            invalid_review = audio(True)
            self.assertEqual(invalid_review["detail"], "external_research_review_unavailable")
            self.assertEqual(invalid_review["external_sources"], [])

    def test_academic_article_can_integrate_the_titles_from_the_previous_turn(self) -> None:
        question = "Está bien, ahora podés buscarme algún artículo académico que englobe estos títulos."
        earlier = "Los ejes son phármakon, memoria técnica y juicio docente en la educación."
        origin = "Stiegler discute la filosofía de la técnica y la educación."
        criteria = ["Phármakon", "Memoria técnica", "Juicio docente"]
        query = "phármakon memoria técnica juicio docente educación"
        sources = [
            {
                "title": "Juicio docente en la educación",
                "url": "https://example.org/docencia",
                "note": "Análisis del juicio docente.",
            },
            {
                "title": "Phármakon, memoria técnica y juicio docente",
                "url": "https://example.org/interseccion",
                "note": "Relaciona phármakon, memoria técnica y juicio docente.",
            },
        ]
        stt = NullSTTProvider(question)
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            app.load_text("prueba", "Lectura", origin, prefetch=False)
            app.conversation.provider = NullChatProvider(earlier)
            app.set_reasoning_mode("normal")
            app.conversation.plan_research_topics = plan = Mock(
                return_value=json.dumps({"criteria": criteria, "topics": [{"topic": "Cruce", "query": query}]})
            )
            coverage = [
                {"index": 1, "evidence": "Phármakon"},
                {"index": 2, "evidence": "memoria técnica"},
                {"index": 3, "evidence": "juicio docente"},
            ]
            app.conversation.review_research_sources = review = Mock(
                return_value=json.dumps(
                    {"coverage": [{"id": 1, "criteria": coverage[2:]}, {"id": 2, "criteria": coverage}]}
                )
            )
            app._research_service.bridge = bridge = NullExternalResearchBridge(
                ExternalResearchResult(True, provider="searxng", sources=sources)
            )
            scope = str(uuid4())
            turn = {"laboratory_session": scope, "document_generation": app._document_generation}

            def submit(message: str, enabled: bool = True) -> dict:
                try:
                    return self._request(
                        base, "/api/chat", {**turn, "message": message, "allow_external_research": enabled}
                    )[1]
                except urllib.error.HTTPError as error:
                    with error:
                        return json.load(error)

            self.assertEqual(submit(question)["detail"], "external_research_topic_required")
            self.assertEqual(bridge.calls, [])
            self.assertEqual(submit("Listá los ejes.")["answer"], earlier)
            self.assertEqual(submit(question, False)["detail"], "external_research_permission_required")
            self.assertEqual(bridge.calls, [])
            self.assertEqual(submit(question)["external_sources"][0]["url"], sources[1]["url"])
            self.assertEqual(plan.call_args.args[1], earlier)
            self.assertIn(question, plan.call_args.args[0])
            self.assertEqual(review.call_args.args[0][0]["criteria"], criteria)
            self.assertEqual(bridge.calls, [(query, {})])

            submit("Volvé a enumerar los ejes del bloque 1.")
            review.return_value = json.dumps(
                {"coverage": [{"id": 1, "criteria": coverage[2:]}, {"id": 2, "criteria": coverage[:2]}]}
            )
            voice = urllib.request.Request(
                f"{base}/api/dialogue/turn?filename=turn.wav&laboratory_session={scope}&allow_external_research=1",
                data=b"RIFF synthetic",
                headers={"Content-Type": "audio/wav"},
            )
            with urllib.request.urlopen(voice, timeout=6) as response:
                partial = json.load(response)
            self.assertEqual(partial["detail"], "external_research_partial_coverage")
            self.assertEqual(plan.call_args.args[1], f"{earlier}\n\nPasaje de origen de la lista:\n{origin}")
            self.assertEqual([source["url"] for source in partial["external_sources"]], [sources[1]["url"]])
            self.assertIn("Cobertura pendiente de confirmar: Juicio docente", partial["answer"])

            submit("Volvé a enumerar los ejes.")
            review.return_value = json.dumps(
                {
                    "coverage": [
                        {"id": 1, "criteria": [{"index": 1, "evidence": "Análisis del juicio docente"}]},
                        {
                            "id": 2,
                            "criteria": [{"index": 1, "evidence": "El phármakon constituye la base del artículo"}],
                        },
                    ]
                }
            )
            missing = submit(question)
            self.assertEqual(missing["detail"], "external_research_no_integrative_source")
            self.assertEqual(missing["external_provider"], "searxng")
            self.assertEqual(missing["external_sources"], [])

    def test_written_contextual_search_uses_the_block_year_and_search_receipt(self) -> None:
        message = "buscá información sobre educación 2026 referido a esto"
        passage = "La educación discute la influencia de la inteligencia artificial en la autoría académica."
        query = "educación inteligencia artificial autoría"
        url = "https://example.org/investigacion"
        provenance_question = "¿Cómo los buscaste y cómo los encontraste?"
        retry_question = "Ok, ¿y podrías recuperar mi confianza buscando realmente en la red?"
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=NullSTTProvider(provenance_question))
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            app.load_text("prueba", "Lectura", passage, prefetch=False)
            app.conversation.plan_research_topics = plan = Mock(
                return_value=json.dumps({"topics": [{"topic": "Educación e IA", "query": query}]})
            )
            app.conversation.review_research_sources = Mock(return_value='{"relevant":[1]}')
            app._research_service.bridge = bridge = NullExternalResearchBridge(
                ExternalResearchResult(
                    True,
                    provider="searxng",
                    sources=[
                        {"title": "Educación e inteligencia artificial", "url": url, "engine": "semantic scholar"}
                    ],
                )
            )
            turn = {"laboratory_session": str(uuid4()), "document_generation": app._document_generation}

            def submit(text: str, enabled: bool = True) -> dict:
                try:
                    return self._request(
                        base, "/api/chat", {**turn, "message": text, "allow_external_research": enabled}
                    )[1]
                except urllib.error.HTTPError as error:
                    with error:
                        return json.load(error)

            self.assertEqual(submit(message, False)["detail"], "external_research_permission_required")
            self.assertEqual(bridge.calls, [])
            found = submit(message)
            self.assertEqual(found["external_provider"], "searxng")
            self.assertEqual(found["external_query"], f"{query} 2026")
            self.assertEqual(plan.call_args.args, (message, passage))
            self.assertEqual(found["external_sources"][0]["url"], url)
            proof = submit("¿Y cómo hiciste la búsqueda?")
            self.assertIn("SearXNG", proof["answer"])
            self.assertIn(f"{query} 2026", proof["answer"])
            submit("Escúchame.")
            proof = submit(provenance_question, False)
            self.assertEqual(proof["detail"], "external_research_provenance")
            self.assertIn("semantic scholar", proof["answer"])
            self.assertIn("Seleccionado por su relación con «Educación e IA»", proof["answer"])
            self.assertIn("descarga o lectura del archivo completo", proof["answer"])
            self.assertEqual(proof["external_sources"][0]["url"], url)
            voice = urllib.request.Request(
                f"{base}/api/dialogue/turn?filename=turn.wav&laboratory_session={turn['laboratory_session']}",
                data=b"RIFF synthetic",
                headers={"Content-Type": "audio/wav"},
            )
            with urllib.request.urlopen(voice, timeout=6) as response:
                audio_proof = json.load(response)
            self.assertEqual(audio_proof["detail"], "external_research_provenance")
            self.assertEqual(audio_proof["external_query"], proof["external_query"])
            self.assertEqual(audio_proof["external_sources"], proof["external_sources"])
            self.assertIn("descarga o lectura del archivo completo", audio_proof["spoken_answer"])
            self.assertIn("Seleccionado por su relación con «Educación e IA»", audio_proof["spoken_answer"])
            self.assertNotIn(url, audio_proof["spoken_answer"])
            self.assertEqual(len(bridge.calls), 1)
            submit("Pero eso lo acabaste de hacer vos misma.")
            self.assertEqual(submit(retry_question)["external_query"], f"{query} 2026")
            self.assertEqual(len(bridge.calls), 2)

            generic = "Bien, puedes buscar información académica y dejarme los links, buscarla en la red."
            self.assertEqual(submit(generic)["external_query"], query)
            self.assertEqual(plan.call_args.args, (generic, passage))
            complex_request = (
                "Hola, ¿podés ver el bloque 1? ¿Podés resumir las principales ideas del bloque "
                "y de cada una de esas ideas buscar información académica en internet y dejarme los links?"
            )
            self.assertEqual(submit(complex_request)["external_query"], query)
            submit("Eso no fue lo que pedí.")
            self.assertEqual(
                submit("Bueno, está muy bien, entonces repáralo ahora y búscalos.")["external_query"], query
            )
            self.assertEqual(len(bridge.calls), 5)

    def test_laboratory_research_uses_spoken_context_and_retries_the_same_request(self) -> None:
        prior = "Los temas son la autoría, la soberanía cognitiva y la delegación del juicio docente."
        passage = "El bloque propone discutir la autoría, la soberanía cognitiva y la delegación del juicio."
        query = "soberanía cognitiva delegación juicio"
        first = (
            "Bueno, escúchame, toma lo que dice en el bloque 1, lo que habías dicho antes, "
            "que separaste los temas y busca información académica sobre esos temas."
        )
        second = "Ok, ¿podés buscar información académica sobre esos puntos?"
        retry = "A ver, intentá ahora de nuevo."
        stt = NullSTTProvider(retry)
        unavailable = ExternalResearchResult(
            False, answer="El buscador tardó demasiado.", provider="searxng", detail="searxng_timeout"
        )
        article = {
            "title": "Soberanía cognitiva y delegación del juicio",
            "url": "https://example.org/juicio",
            "note": "Investigación sobre el juicio docente.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            app = server.context.app
            app.conversation.provider = NullChatProvider(prior)
            app.conversation.plan_research_topics = plan = Mock(
                return_value=json.dumps({"topics": [{"topic": "Juicio", "query": query}]})
            )
            app.conversation.review_research_sources = Mock(return_value='{"relevant":[1]}')
            app._research_service.bridge = bridge = NullExternalResearchBridge(unavailable)
            app.load_text("prueba", "expo final.pdf", passage, prefetch=False)
            app.set_reasoning_mode("normal")
            base = f"http://127.0.0.1:{server.server_port}"
            scope = str(uuid4())
            turn = {"laboratory_session": scope, "document_generation": app._document_generation}
            voice = urllib.request.Request(
                f"{base}/api/dialogue/turn?filename=turn.wav&laboratory_session={scope}&allow_external_research=1",
                data=b"RIFF synthetic",
                headers={"Content-Type": "audio/wav"},
            )

            def audio(text: str) -> dict:
                stt.text = text
                with urllib.request.urlopen(voice, timeout=6) as response:
                    return json.load(response)

            self.assertFalse(audio(retry).get("external_research", False))
            _, structured = self._request(
                base, "/api/chat", {**turn, "message": "Separá los temas del primer bloque en una lista breve."}
            )
            self.assertEqual(structured["answer"], prior)

            self.assertEqual(audio(first)["detail"], "searxng_timeout")
            self.assertEqual(plan.call_args.args[1], passage)
            self.assertIn(prior, plan.call_args.args[0])
            self.assertEqual(audio(second)["detail"], "searxng_timeout")
            self.assertEqual(plan.call_args.args, (second, prior))

            bridge.result = ExternalResearchResult(
                True, provider="searxng", sources=[article, {**article, "url": "https://example.org/duplicate"}]
            )
            app.conversation.review_research_sources.return_value = '{"relevant":[1,2]}'
            result = audio(retry)
            self.assertEqual(result["detail"], "external_research_ok")
            self.assertEqual(result["external_provider"], "searxng")
            self.assertEqual(result["trace"]["external_provider"], "searxng")
            self.assertEqual(plan.call_args.args, (second, prior))
            self.assertEqual([source["url"] for source in result["external_sources"]], [article["url"]])
            self.assertEqual(bridge.calls, [(query, {})] * 3)

            self._request(base, "/api/chat", {**turn, "message": "Gracias."})
            self.assertFalse(audio(retry).get("external_research", False))
            self.assertEqual(len(bridge.calls), 3)

    def test_laboratory_continuity_across_channels_and_context_boundaries(self) -> None:
        stt = NullSTTProvider("¿Podés desarrollar esa interpretación?")
        local = NullChatProvider("Interpretación local privada del texto.")
        cloud = NullChatProvider("Tesis pública de la autonomía escolar.")
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            app.conversation.provider = SelectableChatProvider({"openai": cloud, "local": local}, "local")
            app.set_reasoning_mode("normal")
            app.load_text("opening", "Doc de prueba", " PASAJE_DE_PRUEBA. " * 140, prefetch=False)
            scope = str(uuid4())
            turn = {"laboratory_session": scope, "document_generation": app._document_generation}

            self._request(base, "/api/chat", {**turn, "message": "IDEA_LOCAL_PRIVADA."})
            self._request(base, "/api/chat", {**turn, "message": "¿Qué opinás del pasaje?", "provider": "openai"})
            self.assertNotIn("IDEA_LOCAL_PRIVADA", str(cloud.calls[-1][0]))

            voice = urllib.request.Request(
                base + f"/api/dialogue/turn?filename=voice.wav&provider=openai&laboratory_session={scope}",
                data=b"RIFF synthetic",
                headers={"Content-Type": "audio/wav"},
            )
            with urllib.request.urlopen(voice, timeout=5.0) as response:
                self.assertEqual(json.load(response)["transcript"], stt.text)
            prompt = cloud.calls[-1][0]
            self.assertIn({"role": "assistant", "content": cloud.answer}, prompt)
            self.assertNotIn("IDEA_LOCAL_PRIVADA", str(prompt))
            self.assertNotIn("DOCUMENTO COMPLETO DISPONIBLE", str(prompt))

            app._research_service.bridge = NullExternalResearchBridge(
                ExternalResearchResult(
                    True,
                    answer="CONSULTA_INTERNA_DE_PRUEBA",
                    sources=[
                        {
                            "title": "Fuente sobre autonomía",
                            "url": "https://example.org/paper",
                            "note": "EXTRACTO_PRIVADO",
                        }
                    ],
                )
            )
            self._request(
                base,
                "/api/chat",
                {
                    **turn,
                    "provider": "openai",
                    "message": "Buscá fuentes sobre CONSULTA_INTERNA_DE_PRUEBA.",
                    "allow_external_research": True,
                },
            )
            self._request(
                base,
                "/api/dialogue/turn",
                {**turn, "provider": "openai", "text": "¿Cuál era el enlace de la fuente que encontraste?"},
            )
            followup = str(cloud.calls[-1][0])
            self.assertIn("https://example.org/paper", followup)
            self.assertNotIn("CONSULTA_INTERNA_DE_PRUEBA", followup)
            self.assertNotIn("EXTRACTO_PRIVADO", followup)

            other_window = {**turn, "provider": "openai", "laboratory_session": str(uuid4())}
            self._request(base, "/api/chat", {**other_window, "message": "Retomá lo de antes."})
            self.assertNotIn(cloud.answer, str(cloud.calls[-1][0]))
            self._request(base, "/api/laboratory/reset", {"laboratory_session": scope})
            self._request(base, "/api/chat", {**turn, "message": "Retomá lo de antes.", "provider": "openai"})
            self.assertNotIn(cloud.answer, str(cloud.calls[-1][0]))

            self._request(base, "/api/laboratory/mode", {"mode": "free"})
            self._request(
                base, "/api/chat", {**turn, "message": "Charlemos sobre el contexto de Kant.", "provider": "openai"}
            )
            self.assertNotIn(cloud.answer, str(cloud.calls[-1][0]))
            self.assertNotIn("PASAJE_DE_PRUEBA", str(cloud.calls[-1][0]))

            app.load_text("new", "Nuevo texto", "OTRO_PASAJE.", prefetch=False)
            count = len(cloud.calls)
            _, stale = self._request(base, "/api/chat", {**turn, "message": "¿Qué dice?", "provider": "openai"})
            self.assertEqual(stale["detail"], "laboratory_document_changed")
            self.assertEqual(count, len(cloud.calls))
            self._request(
                base,
                "/api/chat",
                {
                    **turn,
                    "document_generation": app._document_generation,
                    "message": "Sigamos charlando.",
                    "provider": "openai",
                },
            )
            self.assertNotIn(cloud.answer, str(cloud.calls[-1][0]))

    def test_laboratory_interprets_actions_on_the_same_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            model = NullChatProvider("La autonomía exige justificación pública.")
            bridge = NullExternalResearchBridge()
            app.conversation.provider = model
            app._research_service.bridge = bridge
            app.set_reasoning_mode("normal")
            first = "Autonomía y razón pública. " * 70
            second = "La delegación del juicio. " * 70
            app.load_text("charla", "charla.pdf", f"{first}\n\n{second}", prefetch=False)
            app.jump(2)
            turn = {"laboratory_session": str(uuid4()), "document_generation": app._document_generation}

            self._request(base, "/api/chat", {**turn, "message": "¿Qué dice este bloque?", "chunk_index": 0})
            context = model.calls[-1][0][1]["content"]
            self.assertIn("Bloque visible: 1", context)
            self.assertEqual(app.session.cursor, 1)

            _, note = self._request(
                base, "/api/dialogue/turn", {**turn, "text": "Tomá nota de eso que acabás de decir."}
            )
            self.assertEqual(note["note"]["text"], model.answer)
            _, discussion = self._request(
                base, "/api/dialogue/turn", {**turn, "text": "La idea del juicio es importante, no guardes eso."}
            )
            self.assertNotIn("note", discussion)

            _, result = self._request(
                base,
                "/api/dialogue/turn",
                {
                    **turn,
                    "text": "Buscá en internet sobre lo que acabás de decir.",
                    "allow_external_research": True,
                },
            )
            self.assertIn("autonomía", result["external_query"])
            self.assertEqual(bridge.calls[-1][1], {})
            self.assertEqual(len(bridge.calls), 1)

            self._request(base, "/api/chat", {**turn, "message": "Compará el bloque 1 con el bloque 2."})
            prompt = str(model.calls[-1][0])
            self.assertIn("PASAJES PEDIDOS PARA COMPARAR", prompt)
            self.assertIn("Autonomía y razón pública", prompt)
            self.assertIn("delegación del juicio", prompt)
            self.assertEqual(app.session.cursor, 1)

            self._request(base, "/api/chat", {**turn, "message": "Mirá el primer bloque, a ver qué te parece."})
            _, focused = self._request(base, f"/api/status?laboratory_session={turn['laboratory_session']}")
            self.assertEqual(focused["laboratory_focus"]["chunk_number"], 1)
            _, other = self._request(base, f"/api/status?laboratory_session={uuid4()}")
            self.assertEqual(other["laboratory_focus"], {})
            _, cloud = self._request(
                base, f"/api/status?provider=openai&laboratory_session={turn['laboratory_session']}"
            )
            self.assertEqual(cloud["laboratory_focus"], {})
            self.assertIn("FOCO ACTUAL DEL LABORATORIO", str(model.calls[-1][0]))
            self._request(
                base,
                "/api/dialogue/turn",
                {**turn, "text": "Compará este bloque con el primer bloque.", "chunk_index": 1},
            )
            context = next(
                message["content"] for message in model.calls[-1][0] if "PASAJES PEDIDOS" in message["content"]
            )
            comparison = context.split("PASAJES PEDIDOS PARA COMPARAR:", 1)[1]
            self.assertLess(comparison.index("Bloque 2"), comparison.index("Bloque 1"))
            self.assertEqual(app.session.cursor, 1)
            self.assertEqual(app._dialogue_history, [])

            _, missing = self._request(
                base,
                "/api/chat",
                {
                    **turn,
                    "message": "Compará el bloque 1 de ausente.pdf con el bloque 2.",
                },
            )
            self.assertEqual(missing["detail"], "compare_target_not_found")
            _, missing = self._request(
                base,
                "/api/chat",
                {
                    **turn,
                    "message": "Buscá autonomía en ausente.pdf.",
                },
            )
            self.assertEqual(missing["detail"], "search_no_matches")
            self.assertEqual(len(bridge.calls), 1)

    def test_turn_provider_is_pinned_to_the_request_and_legacy_requests_stay_local(self) -> None:
        local = NullChatProvider('{"text":"Texto mejorado"}')
        cloud = NullChatProvider('{"text":"Texto en la nube"}')
        assistant = DictationAssistant({"local": local, "openai": cloud}, selected="openai")
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), dictation_assistant=assistant)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            app.conversation.provider = SelectableChatProvider({"local": local, "openai": cloud}, "openai")
            body = {"text": "Texto original", "requests": ["Mejorá el texto"]}
            status, result = self._request(base, "/api/dictation/review", {**body, "provider": "local"})
            self.assertEqual((status, result["text"]), (200, "Texto mejorado"))
            with self.assertRaises(urllib.error.HTTPError):
                self._request(base, "/api/dictation/review", body)
            self.assertEqual(cloud.calls, [])
            self._request(base, "/api/chat", {"message": "Una pregunta privada de prueba."})
            self.assertEqual(cloud.calls, [])
            self._request(base, "/api/chat", {"message": "Una pregunta distinta.", "provider": "openai"})
            self.assertTrue(cloud.calls)
            self.assertNotIn("Una pregunta privada de prueba.", str(cloud.calls))
            self.assertEqual(assistant.selected, "openai")

    def test_spoken_search_after_a_sentence_uses_only_the_news_topic(self) -> None:
        request_text = (
            "A ver, quiero probar algo que nunca probé. "
            "Busca en internet las principales noticias de geopolítica de hoy."
        )
        stt = NullSTTProvider()
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            app.set_laboratory_mode("free")
            app.conversation.provider = model = NullChatProvider("Respuesta conversacional.")
            app._research_service.bridge = bridge = NullExternalResearchBridge(
                ExternalResearchResult(True, answer="Resultados de SearXNG.", provider="searxng")
            )
            bridge.research_news = news = Mock(wraps=bridge.research_news)
            session = str(uuid4())
            scope = {"laboratory_session": session, "provider": "openai"}

            def chat(text: str, allowed: bool = True) -> dict:
                try:
                    return self._request(
                        base, "/api/chat", {**scope, "message": text, "allow_external_research": allowed}
                    )[1]
                except urllib.error.HTTPError as error:
                    with error:
                        return json.load(error)

            def speak(text: str, allowed: bool = True) -> dict:
                stt.text = text
                request = urllib.request.Request(
                    f"{base}/api/dialogue/turn?filename=turn.wav&laboratory_session={session}"
                    f"&provider=openai&allow_external_research={int(allowed)}",
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.load(response)

            for submit in (chat, speak):
                with self.subTest(submit=submit.__name__):
                    bridge.calls.clear()
                    for text in (
                        "Ahora te leo una consigna. " + request_text.split(". ", 1)[1],
                        "Esto es un ejemplo. Buscá en internet noticias. ¿Qué significa esa instrucción?",
                        "Si te pidiera esto. Busca en internet las noticias de hoy.",
                        "La consigna decía: «Buscá en internet noticias».",
                        "No busques en internet. Busca en internet las noticias de hoy.",
                        "Buscá en internet noticias. Pero quedate con lo de acá, sin usar internet.",
                    ):
                        submit(text)
                    self.assertEqual(bridge.calls, [])
                    self.assertEqual(
                        submit("Me pregunto si podrías buscar en internet sobre geopolítica.")["detail"],
                        "external_research_intent_unclear",
                    )
                    self.assertEqual(bridge.calls, [])
                    self.assertEqual(submit(request_text, False)["detail"], "external_research_permission_required")
                    self.assertEqual(bridge.calls, [])
                    self.assertIn("desmarcada", submit("¿Por qué no podés buscar en internet?", False)["answer"])
                    previous_model_calls = len(model.calls)
                    found = submit(request_text)
                    self.assertEqual(found["external_query"], "geopolítica")
                    self.assertEqual(found["external_provider"], "searxng")
                    news.assert_called_with("geopolítica", today=True)
                    proof = submit("¿Por qué no buscaste en internet?")
                    self.assertEqual(proof["detail"], "external_research_provenance")
                    self.assertIn("geopolítica", proof["answer"])
                    self.assertEqual(len(model.calls), previous_model_calls)
                    self.assertEqual(bridge.calls, [("geopolítica", {})])

    def test_external_research_permission_is_per_request_and_prohibition_wins(self) -> None:
        affirmative = "Buscá fuentes sobre el concepto de técnica."
        prohibitions = (
            "Buscá fuentes, pero no quiero que busques en internet.",
            "Buscá fuentes sobre la escuela, pero no lo hagas en internet.",
            "Buscá fuentes sobre la escuela, pero no la hagas por la web.",
            "Buscá fuentes sobre la escuela, pero evitá conectarte a internet.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            stt = NullSTTProvider()
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            bridge = NullExternalResearchBridge()
            server.context.app._research_service.bridge = bridge
            for path, key in (("/api/chat", "message"), ("/api/dialogue/turn", "text")):
                self._request(base, path, {key: affirmative})
                self._request(base, path, {key: affirmative, "allow_external_research": "true"})
                for prohibition in prohibitions:
                    self._request(base, path, {key: prohibition, "allow_external_research": True})
                self.assertEqual(bridge.calls, [])
                self._request(base, path, {key: affirmative, "allow_external_research": True})
                self.assertEqual(bridge.calls, [(affirmative, {})])
                bridge.calls.clear()
                for provider in ("gemma", "lfm"):
                    self._request(base, path, {key: affirmative, "provider": provider, "allow_external_research": True})
                    self.assertEqual(bridge.calls, [(affirmative, {})])
                    bridge.calls.clear()

            for utterance in (*prohibitions, affirmative):
                stt.text = utterance
                request = urllib.request.Request(
                    base + "/api/dialogue/turn?filename=turn.wav&allow_external_research=1",
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    self.assertEqual(response.status, 200)
            self.assertEqual(bridge.calls, [(affirmative, {})])

    def test_dialogue_resolves_the_active_document_and_clarifies_web_search_scope(self) -> None:
        stt = NullSTTProvider()
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            model = NullChatProvider("El bloque presenta un dilema filosófico.")
            bridge = NullExternalResearchBridge()
            app.conversation.provider = model
            app._research_service.bridge = bridge
            app.set_reasoning_mode("normal")
            app.load_text(
                "expo",
                "expo final.pdf",
                make_reading_sections(("Primer tramo", "La frontera dentada."), ("Segundo tramo", "Otra idea.")),
                prefetch=False,
            )
            app.add_reference_text("ref", "Consulta.txt", "Referencia separada.")
            app.jump(2)

            def speak(utterance: str) -> dict:
                stt.text = utterance
                request = urllib.request.Request(
                    base + "/api/dialogue/turn?filename=turn.wav&allow_external_research=1",
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    return json.load(response)

            spoken = speak("Mirá el bloque 1 del documento 1.")
            self.assertTrue(spoken["ok"])
            self.assertIn("dilema filosófico", spoken["answer"])
            _, written = self._request(base, "/api/chat", {"message": "Mirá el bloque 1 del documento, por favor."})
            self.assertTrue(written["ok"])
            self.assertEqual(app.laboratory_focus_status()["doc_id"], "expo")
            self.assertEqual(app.laboratory_focus_status()["chunk_number"], 1)
            self.assertEqual(app.session.status()["current"], 2)
            for messages, *_ in model.calls:
                prompt = "\n".join(item["content"] for item in messages)
                self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)
                self.assertIn("La frontera dentada.", prompt)

            missing = speak("Mirá el bloque 1 del documento 9.")
            self.assertTrue(missing["ok"])
            self.assertEqual(missing["detail"], "document_not_found")
            self.assertIn("nombre", missing["answer"])
            self.assertEqual(len(model.calls), 2)

            external = speak("¿Podés buscar en internet sobre lo que dice la primera página del documento 1?")
            self.assertEqual(external["detail"], "external_research_topic_required")
            self.assertIn("tema", external["answer"])
            self.assertEqual(bridge.calls, [])

    def test_web_search_follows_the_last_answer_in_the_same_conversation(self) -> None:
        request_text = "Bien, escúchame. Quiero que busques en internet información sobre esto que me acabas de decir."
        stt = NullSTTProvider(request_text)
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            bridge = NullExternalResearchBridge()
            app._research_service.bridge = bridge
            app.conversation.provider = NullChatProvider(
                "La escritura y el juicio se delegan a la inteligencia artificial."
            )
            app.set_reasoning_mode("normal")
            app.load_text("d", "Original", "CONTENIDO_PRIVADO DEL DOCUMENTO.", prefetch=False)

            def search_by_voice(allowed: bool) -> dict:
                request = urllib.request.Request(
                    f"{base}/api/dialogue/turn?filename=turn.wav&allow_external_research={int(allowed)}",
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    return json.load(response)

            self.assertEqual(search_by_voice(True)["detail"], "external_research_topic_required")
            self.assertEqual(bridge.calls, [])
            self._request(base, "/api/dialogue/turn", {"text": "¿Qué implica esto para la educación?"})
            app.conversation.provider = NullChatProvider("Kant relaciona autonomía y razón.")
            self._request(base, "/api/chat", {"message": "¿Qué plantea Kant?"})
            self.assertEqual(search_by_voice(False)["detail"], "external_research_permission_required")
            self.assertEqual(bridge.calls, [])

            voice_result = search_by_voice(True)
            voice_query, snapshot = bridge.calls[-1]
            self.assertEqual(voice_result["external_query"], voice_query)
            self.assertIn("escritura", voice_query)
            self.assertIn("juicio", voice_query)
            self.assertIn("inteligencia artificial", voice_query)
            self.assertNotIn("Kant", voice_query)
            self.assertNotIn("CONTENIDO_PRIVADO", voice_query)
            self.assertEqual(snapshot, {})

            _, chat_result = self._request(
                base, "/api/chat", {"message": request_text, "allow_external_research": True}
            )
            self.assertEqual(chat_result["external_query"], "Kant relaciona autonomía razón")
            self.assertEqual(bridge.calls[-1], (chat_result["external_query"], {}))

    def test_spoken_research_correction_uses_the_requested_block(self) -> None:
        correction = (
            "No, lo que te pedí es que con respecto al primer bloque del documento busques información en internet."
        )
        stt = NullSTTProvider()
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), stt=stt)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            model = NullChatProvider("Conversación normal.")
            bridge = NullExternalResearchBridge()
            app.conversation.provider = model
            app._research_service.bridge = bridge
            app.set_reasoning_mode("normal")
            first = "Inteligencia artificial y evaluación escolar. contacto@privado.example. " + (
                "La escritura escolar exige que cada estudiante argumente su posición. " * 25
            )
            second = "Astronomía y espectroscopía. " + (
                "Cada observatorio reúne mediciones separadas por distancia. " * 33
            )
            app.load_text("expo", "expo de prueba.pdf", f"{first}\n\n{second}", prefetch=False)
            app.jump(2)

            def speak(utterance: str, *, allowed: bool = True) -> dict:
                stt.text = utterance
                request = urllib.request.Request(
                    f"{base}/api/dialogue/turn?filename=turn.wav&allow_external_research={int(allowed)}",
                    data=b"RIFF synthetic",
                    headers={"Content-Type": "audio/wav"},
                )
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    return json.load(response)

            self.assertEqual(
                speak("Bien, pero ahora de buscar información en internet.")["detail"],
                "external_research_topic_required",
            )
            self.assertEqual(speak(correction, allowed=False)["detail"], "external_research_permission_required")
            self.assertEqual(bridge.calls, [])
            self.assertEqual(model.calls, [])

            result = speak(correction)
            query, snapshot = bridge.calls[-1]
            self.assertTrue(result["external_research"])
            self.assertEqual(result["external_query"], query)
            self.assertIn("Inteligencia artificial evaluación escolar", query)
            self.assertNotIn("contacto", query)
            self.assertNotIn("Astronomía", query)
            self.assertEqual(snapshot, {})
            self.assertEqual(app.session.status()["current"], 2)

            _, result = self._request(
                base,
                "/api/chat",
                {
                    "message": "Buscá en internet sobre el bloque dos de expo de prueba.pdf.",
                    "allow_external_research": True,
                },
            )
            self.assertIn("Astronomía espectroscopía", result["external_query"])
            self.assertEqual(
                speak("Buscá en internet sobre el bloque 1 de otro archivo.pdf.")["detail"],
                "external_research_block_unavailable",
            )
            self.assertEqual(
                speak("Buscá en internet sobre el bloque 20 del documento.")["detail"],
                "external_research_block_unavailable",
            )
            self.assertEqual(len(bridge.calls), 2)

    def test_library_homonyms_keep_legacy_notes_separate_even_after_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            library = server.context.library_root
            first = library / "uno" / "clase.txt"
            second = library / "dos" / "clase.txt"
            shared = "Encabezado compartido."
            for path in (first, second):
                path.parent.mkdir(parents=True)
                path.write_text(shared, encoding="utf-8")
            (library / "intermedio.txt").write_text("Otro archivo abierto entre los dos.", encoding="utf-8")
            app.load_text("clase", first.name, shared, prefetch=False, source_path=str(first), source_type="file")
            original = app.create_note("Nota de la primera carpeta")["note"]
            app.notes.add("clase", second.name, 0, "Nota de la segunda carpeta", quote=shared)
            with managed_test_app(root=root / "app") as restored:
                self.assertNotEqual(restored.status()["doc_id"], "clase")
                self.assertEqual(restored.list_notes()["items"], [])
                self.assertTrue(restored.notes_summary()["legacy_archive_available"])
                self.assertEqual(len(restored.list_legacy_notes(restored.status()["doc_id"])["items"]), 2)

            self._request(base, "/api/load", {"book_id": "intermedio.txt"})
            _, opened = self._request(base, "/api/load", {"book_id": "uno/clase.txt"})
            self.assertEqual(opened["notes"]["count"], 0)
            doc_id = opened["doc_id"]
            archive = "/api/notes/legacy?doc_id=" + doc_id
            self.assertTrue(opened["notes"]["legacy_archive_available"])
            _, candidates = self._request(base, archive)
            self.assertEqual(
                {note["text"] for note in candidates["items"]},
                {
                    "Nota de la primera carpeta",
                    "Nota de la segunda carpeta",
                },
            )
            self.assertTrue(all(note["can_copy"] for note in candidates["items"]))
            self._request(base, "/api/notes/legacy/copy", {"doc_id": doc_id, "note_id": original["note_id"]})
            self._request(base, "/api/notes/legacy/copy", {"doc_id": doc_id, "note_id": original["note_id"]})
            self.assertEqual([note["note_id"] for note in app.list_notes()["items"]], [original["note_id"]])
            self.assertEqual(len(self._request(base, archive)[1]["items"]), 1)
            _, reference = self._request(base, "/api/load", {"book_id": "dos/clase.txt", "role": "reference"})
            self.assertTrue(reference["ok"])
            self.assertNotEqual(reference["reference_added"]["doc_id"], doc_id)
            _, other = self._request(base, "/api/load", {"book_id": "dos/clase.txt"})
            self.assertEqual(other["notes"]["count"], 0)
            with self.assertRaises(urllib.error.HTTPError) as stale:
                self._request(base, "/api/notes/legacy/copy", {"doc_id": doc_id, "note_id": original["note_id"]})
            self.assertEqual(stale.exception.code, 409)
            self.assertEqual(len(app.list_notes(doc_id="clase")["items"]), 2)

    def test_old_automatic_copy_is_reviewed_without_losing_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "clase.txt"
            path.write_text("Un bloque compartido.", encoding="utf-8")
            with managed_test_app(root=root / "app") as app:
                original = app.notes.add("clase", path.name, 0, "Versión de origen", quote=path.read_text())
                doc_id = app.load_file(path, prefetch=False)["doc_id"]
                app.notes._store(doc_id).write(
                    {
                        "doc_id": doc_id,
                        "recovered_from": ["clase"],
                        "notes": [{**original, "doc_id": doc_id, "text": "Versión editada después de la copia"}],
                    }
                )
                self.assertEqual(app.list_notes()["items"], [])
                pending = app.list_legacy_notes(doc_id)["items"]
                self.assertEqual([note["text"] for note in pending], ["Versión editada después de la copia"])
                self.assertTrue(app.copy_legacy_note(doc_id, original["note_id"])["ok"])
                self.assertTrue(app.update_note(original["note_id"], "Edición posterior a la verificación")["ok"])
            with managed_test_app(root=root / "app") as restored:
                self.assertEqual(
                    [note["text"] for note in restored.list_notes()["items"]],
                    [
                        "Edición posterior a la verificación",
                    ],
                )
                self.assertEqual(restored.notes.list("clase")[0]["text"], "Versión de origen")
                self.assertEqual(restored.list_legacy_notes(doc_id)["items"], [])

    def test_reimport_keeps_the_legacy_archive_and_accepts_long_unicode_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            original = "Texto identificable del archivo anterior."
            converted = server.context.converted_root
            converted.mkdir(parents=True, exist_ok=True)
            legacy_file = converted / "clase.txt"
            legacy_file.write_text(original, encoding="utf-8")
            app.load_text("clase", "clase.txt", original, prefetch=False, source_path=str(legacy_file))
            note = app.create_note("Anotación anterior")["note"]
            app.notes.add("clase", "clase.txt", 0, "Con otro anclaje", quote="Texto de un archivo homónimo.")
            with managed_test_app(root=Path(tmp) / "app") as restored:
                restored_id = restored.status()["doc_id"]
                self.assertNotEqual(restored_id, "clase")
                self.assertTrue(restored.notes_summary()["legacy_archive_available"])
                self.assertEqual(restored.list_notes()["items"], [])

            def reimport(filename: str, text: str) -> dict:
                return self._request(
                    base,
                    "/api/import",
                    {
                        "filename": filename,
                        "data_b64": base64.b64encode(text.encode()).decode(),
                    },
                )[1]

            different = reimport("clase.txt", "Otro archivo homónimo.")
            self.assertEqual(different["notes"]["count"], 0)
            same = reimport("clase.txt", original)
            self.assertEqual(same["doc_id"], restored_id)
            self.assertEqual(same["notes"]["count"], 0)
            _, archive = self._request(base, "/api/notes/legacy?doc_id=" + same["doc_id"])
            self.assertEqual(
                {item["text"]: item["can_copy"] for item in archive["items"]},
                {"Anotación anterior": True, "Con otro anclaje": False},
            )
            self._request(base, "/api/notes/legacy/copy", {"doc_id": same["doc_id"], "note_id": note["note_id"]})
            self.assertEqual(app.notes_summary()["count"], 1)
            self.assertTrue(app.delete_note(note["note_id"])["ok"])
            self.assertEqual(reimport("clase.txt", original)["notes"]["count"], 0)
            self.assertEqual(len(app.list_notes(doc_id="clase")["items"]), 2)
            title = "\U00020000" * 58 + ".txt"
            long_name = reimport(title, original)
            self.assertEqual(long_name["title"], title)
            self.assertLessEqual(len(os.fsencode(Path(long_name["converted_text"]).name)), 255)

    def test_editing_saves_a_word_copy_and_uses_it_for_reading(self) -> None:
        from docx import Document as WordDocument

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_port}"
            original = root / "Guion.docx"
            source = WordDocument()
            source.add_paragraph("Comienzo del documento.")
            source.add_paragraph("Segunda sección de lectura filosófica. " * 270)
            source.add_paragraph("Último párrafo del documento.")
            source.save(original)
            initial_bytes = original.read_bytes()
            _, imported = self._request(
                base,
                "/api/import",
                {
                    "filename": original.name,
                    "data_b64": base64.b64encode(initial_bytes).decode(),
                },
            )
            self.assertGreater(imported["total"], 1)
            _, snapshot = self._request(base, "/api/reader/edit")
            self.assertIn("Último párrafo del documento.", snapshot["text"])
            self.assertIn("\n\n", snapshot["text"])

            downloads = server.context.settings.paths.downloads
            downloads.mkdir(parents=True, exist_ok=True)
            existing = downloads / "Guion - editado.docx"
            existing.write_bytes(initial_bytes)

            ending = "  Cierre   corregido.\t&copy;\nLínea nueva.\n\n\n\nÚltima línea.  "
            text = snapshot["text"].replace("Último párrafo del documento.", ending)
            body = {**snapshot, "text": text}
            app = server.context.app
            limit = app._session_store.max_bytes
            try:
                app._session_store.max_bytes = 256
                with self.assertRaises(urllib.error.HTTPError):
                    self._request(base, "/api/reader/edit", body)
                self.assertEqual(set(downloads.iterdir()), {existing})
                self.assertEqual(app.session.status()["doc_id"], imported["doc_id"])
            finally:
                app._session_store.max_bytes = limit

            _, edited = self._request(base, "/api/reader/edit", body)
            self.assertNotEqual(edited["doc_id"], imported["doc_id"])
            copy = Path(edited["edited_copy"]["path"])
            self.assertEqual(copy.parent, downloads)
            self.assertEqual(copy.suffix, ".docx")
            self.assertNotEqual(copy, existing)
            self.assertEqual(existing.read_bytes(), initial_bytes)
            self.assertEqual(original.read_bytes(), initial_bytes)
            self.assertEqual("\n\n".join(paragraph.text for paragraph in WordDocument(copy).paragraphs), text)

            _, reopened = self._request(base, "/api/reader/edit")
            self.assertEqual(reopened["text"], text)
            self.assertEqual(reopened["doc_id"], edited["doc_id"])
            with managed_test_app(root=root / "app") as restored:
                self.assertEqual(restored.session.status()["doc_id"], edited["doc_id"])
                self.assertEqual(restored.session.document.text, text)
            files = set(downloads.iterdir())
            with self.assertRaises(urllib.error.HTTPError) as stale:
                self._request(base, "/api/reader/edit", body)
            self.assertEqual(stale.exception.code, 409)
            self.assertEqual(set(downloads.iterdir()), files)

    def test_import_homonyms_restore_and_a_failed_upload_preserves_the_active_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app

            def upload(text: str, role: str = "main") -> dict:
                _, result = self._request(
                    base,
                    "/api/import",
                    {"filename": "mismo.txt", "role": role, "data_b64": base64.b64encode(text.encode()).decode()},
                )
                return result

            first = upload("Texto principal de la primera copia.")
            second = upload("Texto alternativo de la segunda copia.", "reference")
            self.assertNotEqual(first["doc_id"], second["reference_added"]["doc_id"])
            self.assertEqual(Path(first["converted_text"]).read_text(), "Texto principal de la primera copia.")
            app._session_store.max_bytes = 4096
            converted = set(server.context.converted_root.iterdir())
            with self.assertRaises(urllib.error.HTTPError):
                upload("Palabras que superan el presupuesto durable. " * 300)
            self.assertEqual(set(server.context.converted_root.iterdir()), converted)
            self.assertEqual(app.session.status()["doc_id"], first["doc_id"])
            with managed_test_app(root=Path(tmp) / "app") as restored:
                self.assertEqual(restored.session.status()["doc_id"], first["doc_id"])
                self.assertEqual(restored.session.document.text, "Texto principal de la primera copia.")

    def test_quick_text_and_dialogue_content_do_not_reach_the_saved_session_or_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_port}"
            app = server.context.app
            self._request(
                base, "/api/load", {"doc_id": "referencia", "text": "Referencia de prueba", "role": "reference"}
            )
            self._request(base, "/api/quick-text", {"text": "Marca temporal reservada para la prueba"})
            self._request(base, "/api/reference/promote", {"doc_id": "referencia"})
            self.assertNotIn("Marca temporal reservada", app.session_state_path.read_text())
            self._request(base, "/api/dialogue/turn", {"text": "¿Qué significa esta frase privada de prueba?"})
            trace = app.dialogue_trace_path
            self.assertNotIn("frase privada de prueba", trace.read_text())
            self.assertEqual(trace.stat().st_mode & 0o777, 0o600)

    def _settings(self, root: Path):
        settings = create_settings(
            repository_root=Path.cwd(),
            environ={
                "HOME": str(root / "home"),
                "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
            },
        )
        settings = replace(settings, ports=replace(settings.ports, api=0))
        return settings

    def _start(
        self, root: Path, *, synthetic_tts: bool = False, stt=None, dictation_assistant=None, bind_host="127.0.0.1"
    ):
        tts = SyntheticWavTTSProvider() if synthetic_tts else None
        app_context = managed_test_app(root=root / "app", tts=tts, stt=stt, dictation_assistant=dictation_assistant)
        app = app_context.__enter__()
        settings = replace(self._settings(root), security=SecuritySettings(bind_host=bind_host))
        server = web_server.create_http_server(app, settings)
        thread = threading.Thread(target=server.serve_forever, name="fusion-test-http")
        thread.start()
        self.addCleanup(app_context.__exit__, None, None, None)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3.0)
        self.addCleanup(server.shutdown)
        return server

    def _request(self, base: str, path: str, payload: dict | None = None, *, method: str | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method or ("POST" if data is not None else "GET"),
        )
        with urllib.request.urlopen(request, timeout=5.0) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            return response.status, json.loads(raw) if "application/json" in content_type and raw else raw

    def test_ipv6_loopback_serves_health_and_reports_effective_port(self) -> None:
        if not socket.has_ipv6:
            self.skipTest("IPv6 unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), bind_host="::1")
            base = f"http://[::1]:{server.server_port}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(base + "/health/live", timeout=3) as response:
                self.assertEqual(json.load(response)["pid"], os.getpid())
            self.assertEqual(server.context.runtime_info["port"], server.server_port)

    def test_importing_compatibility_server_has_no_runtime_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            env = dict(os.environ)
            env.update(
                {
                    "HOME": str(root / "home"),
                    "FUSION_READER_RUNTIME_ROOT": str(runtime),
                    "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import scripts.fusion_reader_v2_server; "
                    "import fusion_reader_v2.web.server as web; "
                    "assert web.APP is None",
                ],
                cwd=Path.cwd(),
                env=env,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(runtime.exists())

    def test_promoted_reference_survives_http_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            first_text = "El documento A conserva su propio argumento."
            second_text = "El documento B presenta una objeción diferente."
            self._request(base, "/api/load", {"doc_id": "A", "title": "Original A", "text": first_text})
            self._request(
                base,
                "/api/load",
                {"doc_id": "B", "title": "Consulta B", "text": second_text, "role": "reference"},
            )
            code, promoted = self._request(base, "/api/reference/promote", {"doc_id": "B"})
            self.assertEqual(code, 200)
            self.assertEqual(promoted["doc_id"], "B")
            self.assertEqual(promoted["text"], second_text)
            self.assertTrue(self.doCleanups())

            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            code, restored = self._request(base, "/api/status")
            self.assertEqual(code, 200)
            self.assertEqual(restored["doc_id"], "B")
            self.assertEqual(restored["title"], "Consulta B")
            self.assertEqual(restored["text"], second_text)
            self.assertEqual([item["doc_id"] for item in restored["reference_documents"]], ["A"])
            for doc_id, text in (("A", first_text), ("B", second_text)):
                code, promoted = self._request(base, "/api/reference/promote", {"doc_id": doc_id})
                self.assertEqual(code, 200)
                self.assertEqual(promoted["doc_id"], doc_id)
                self.assertEqual(promoted["text"], text)
            self.assertTrue(self.doCleanups())

    def test_colliding_note_ids_stay_isolated_through_http_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for doc_id, text in (("libro/a", "Nota A"), ("libro_a", "Nota B")):
                self._request(base, "/api/load", {"doc_id": doc_id, "title": doc_id, "text": "Contenido"})
                code, note = self._request(base, "/api/notes/create", {"text": text})
                self.assertEqual(code, 200)
                self.assertEqual(note["note"]["doc_id"], doc_id)
            self.assertTrue(self.doCleanups())
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for encoded_id, text in (("libro%2Fa", "Nota A"), ("libro_a", "Nota B")):
                code, notes = self._request(base, f"/api/notes?doc_id={encoded_id}")
                self.assertEqual(code, 200)
                self.assertEqual([note["text"] for note in notes["items"]], [text])
            self.assertTrue(self.doCleanups())

    def test_ambiguous_legacy_notes_warning_survives_http_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start(root)
            legacy = root / "app" / "notes" / "book.json"
            raw = b'[{"note_id":"unknown","text":"Sin identidad"}]'
            legacy.write_bytes(raw)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            code, notes = self._request(base, "/api/notes?doc_id=book")
            self.assertEqual(code, 200)
            self.assertEqual(notes["items"], [])
            self.assertIn("legacy_notes_require_review", notes["warnings"])
            self._request(base, "/api/load", {"doc_id": "book", "title": "Book", "text": "Contenido"})
            self._request(base, "/api/notes/create", {"text": "Identificada"})
            self.assertTrue(self.doCleanups())
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            code, notes = self._request(base, "/api/notes?doc_id=book")
            self.assertEqual(code, 200)
            self.assertEqual([note["text"] for note in notes["items"]], ["Identificada"])
            self.assertIn("legacy_notes_require_review", notes["warnings"])
            self.assertEqual(legacy.read_bytes(), raw)
            self.assertTrue(self.doCleanups())

    def test_local_path_boundary_helper_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, managed_test_app(root=Path(tmp) / "app") as app:
            root = Path(tmp)
            context = web_server.WebContext(app=app, settings=self._settings(root), runtime_info={})

            self.assertEqual(web_server.library_items(context), [])
            context.library_root.mkdir(parents=True)
            book = context.library_root / "book.txt"
            book.write_text("Texto de biblioteca", encoding="utf-8")
            (context.library_root / "ignored.pdf").write_bytes(b"%PDF")
            self.assertEqual([item["id"] for item in web_server.library_items(context)], ["book.txt"])

            for invalid in ("", "../escape.txt", str(root / "absolute.txt")):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "invalid_book_id"):
                    web_server.resolve_library_path(context, invalid)

            outside = root / "outside.txt"
            outside.write_text("afuera", encoding="utf-8")
            (context.library_root / "linked.txt").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "book_outside_library"):
                web_server.resolve_library_path(context, "linked.txt")
            with self.assertRaisesRegex(ValueError, "unsupported_book_type"):
                web_server.resolve_library_path(context, "ignored.pdf")
            with self.assertRaisesRegex(FileNotFoundError, "book_not_found"):
                web_server.resolve_library_path(context, "missing.txt")
            self.assertEqual(web_server.resolve_library_path(context, "book.txt"), book)

            cache_file = app.cache.root / "inside.wav"
            cache_file.write_bytes(b"RIFF")
            self.assertEqual(web_server.audio_url_for(context, ""), "")
            self.assertEqual(web_server.audio_url_for(context, str(outside)), "")
            self.assertEqual(web_server.audio_url_for(context, str(app.cache.root / "missing.wav")), "")
            self.assertEqual(web_server.audio_url_for(context, str(cache_file)), "/audio/inside.wav")
            self.assertIsNone(web_server.cached_audio_path(context, "/audio/missing.wav"))
            self.assertEqual(web_server.cached_audio_path(context, "/audio/inside.wav"), cache_file)

            first = web_server.unique_download_target(context, "result.docx")
            self.assertEqual(first.name, "result.docx")
            first.write_bytes(b"docx")
            self.assertEqual(web_server.unique_download_target(context, "result.docx").name, "result_2.docx")

    def test_static_liveness_and_readiness_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for path, marker in (
                ("/", "Panda Fusión".encode("utf-8")),
                ("/static/styles.css", b"--accent"),
                ("/static/panda-fusion-emblem.webp", b"RIFF"),
                ("/static/panda-princess-dialog-hd.webp", b"RIFF"),
                ("/static/app.js", b"bootstrap.mjs"),
                ("/static/js/bootstrap.mjs", b"readCurrent"),
                ("/static/js/dictation.mjs", b"createDictationController"),
                ("/static/js/panda_dialog.mjs", b"createPandaDialog"),
                ("/health/live", b'"status": "live"'),
                ("/health/ready", b'"reader_ready": true'),
            ):
                with self.subTest(path=path), urllib.request.urlopen(base + path, timeout=3.0) as response:
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    if path.endswith(".webp"):
                        self.assertTrue(body.startswith(marker), "La mascota debe entregarse como WebP válido")
                    else:
                        self.assertIn(marker, body)
                    self.assertTrue(response.headers.get("X-Request-ID"))
                    self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    def test_cached_audio_supports_seeking_with_byte_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            audio = server.context.app.cache.root / "seek.wav"
            content = bytes(range(256)) * 1024
            audio.write_bytes(content)
            url = f"http://127.0.0.1:{server.server_address[1]}/audio/seek.wav"
            with urllib.request.urlopen(url, timeout=3.0) as response:
                self.assertEqual(response.read(), content)
                self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
                self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            for requested, start, end in (
                ("bytes=70000-135000", 70000, 135000),
                ("bytes=70000-", 70000, len(content) - 1),
                ("bytes=-127", len(content) - 127, len(content) - 1),
                ("bytes=250000-999999", 250000, len(content) - 1),
            ):
                with self.subTest(requested=requested):
                    request = urllib.request.Request(url, headers={"Range": requested})
                    with urllib.request.urlopen(request, timeout=3.0) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.read(), content[start : end + 1])
                        self.assertEqual(response.headers.get("Content-Length"), str(end - start + 1))
                        self.assertEqual(response.headers.get("Content-Range"), f"bytes {start}-{end}/{len(content)}")
            for requested in ("bytes=999999-", "bytes=10-2", "bytes=-0"):
                with self.subTest(requested=requested), self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url, headers={"Range": requested}), timeout=3.0)
                self.assertEqual(error.exception.code, 416)
                self.assertEqual(error.exception.headers.get("Content-Range"), f"bytes */{len(content)}")
                self.assertEqual(error.exception.read(), b"")
            request = urllib.request.Request(url, headers={"Range": "bytes=10-20"}, method="HEAD")
            with urllib.request.urlopen(request, timeout=3.0) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
                self.assertEqual(response.headers.get("Content-Length"), str(len(content)))
                self.assertEqual(response.read(), b"")

    def test_word_timings_are_bound_to_current_text_voice_and_cached_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), synthetic_tts=True)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            self._request(base, "/api/load", {"doc_id": "timings", "text": "La enseñanza de la filosofía."})
            _, read = self._request(base, "/api/read", {"play": False})
            with patch.object(
                server.context.reading_timings, "start", return_value={"ok": True, "state": "pending"}
            ) as start:
                status, result = self._request(base, "/api/read/timings", {"audio_url": read["audio_url"]})
                self.assertEqual(status, 200)
                self.assertEqual(result["state"], "pending")
                self.assertEqual(start.call_args.args[1], "La enseñanza de la filosofía.")
                for url in ("https://external.example/audio.wav", "/audio/missing.wav"):
                    with self.subTest(url=url), self.assertRaises(urllib.error.HTTPError) as error:
                        self._request(base, "/api/read/timings", {"audio_url": url})
                    self.assertEqual(error.exception.code, 409)
                self._request(base, "/api/load", {"doc_id": "changed", "text": "Otro documento."})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self._request(base, "/api/read/timings", {"audio_url": read["audio_url"]})
                self.assertEqual(error.exception.code, 409)
                self.assertEqual(start.call_count, 1)

    def test_http_server_rejects_non_loopback_security_even_if_settings_are_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for host in ("0.0.0.0", "192.168.1.20", "reader.lan"):
                with self.subTest(host=host), managed_test_app(root=root / host.replace("/", "_")) as app:
                    settings = replace(self._settings(root), security=SecuritySettings(bind_host=host))
                    with self.assertRaises(ConfigurationError):
                        web_server.create_http_server(app, settings)

    def test_two_servers_keep_application_state_and_jobs_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._start(root / "first")
            second = self._start(root / "second")
            self.assertIsNot(first.context, second.context)
            self.assertIsNot(first.context.import_jobs, second.context.import_jobs)
            self.assertIsNone(web_server.APP)

            def load(server, doc_id: str) -> None:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/load",
                    data=json.dumps({"doc_id": doc_id, "title": doc_id, "text": "Texto"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3.0) as response:
                    self.assertEqual(response.status, 200)

            load(first, "first-doc")
            load(second, "second-doc")
            self.assertEqual(first.context.app.status()["doc_id"], "first-doc")
            self.assertEqual(second.context.app.status()["doc_id"], "second-doc")

    def test_status_exposes_bounded_operational_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            url = f"http://127.0.0.1:{server.server_address[1]}/api/status"
            with urllib.request.urlopen(url, timeout=3.0) as response:
                payload = json.loads(response.read())
            for key in (
                "version",
                "commit",
                "pid",
                "uptime_seconds",
                "state_schema",
                "cache",
                "jobs",
                "providers",
                "ports",
                "warnings",
                "degradations",
            ):
                self.assertIn(key, payload)
            self.assertEqual(payload["ports"]["tts_gpu"], 7853)
            self.assertEqual(payload["ports"]["tts_cpu"], 7851)

    def test_malformed_json_and_content_type_return_stable_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            url = f"http://127.0.0.1:{server.server_address[1]}/api/load"
            cases = (
                (b"{", "application/json", "invalid_json"),
                (b"plain", "text/plain", "application_json_required"),
                (b"[]", "application/json", "json_object_required"),
            )
            for body, content_type, error in cases:
                with self.subTest(error=error):
                    request = urllib.request.Request(
                        url,
                        data=body,
                        headers={"Content-Type": content_type},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(request, timeout=3.0)
                    payload = json.loads(raised.exception.read())
                    self.assertEqual(payload["error"], error)
                    self.assertTrue(payload["request_id"])

    def test_dictation_revises_prose_through_the_selected_assistant(self) -> None:
        original = "La casa era blanco blanco.\n\nEl patio tenía 3 plantas."
        revised = "La casa era blanca.\n\nEn el patio había 3 plantas."
        assistant = DictationAssistant({"local": NullChatProvider(json.dumps({"text": revised}))}, selected="local")
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), dictation_assistant=assistant)
            base = f"http://127.0.0.1:{server.server_port}"
            _, instruction = self._request(
                base,
                "/api/dictation/interpret",
                {"text": "Lucy, revisá el texto para hacerlo más legible", "require_wake_word": True},
            )
            self.assertEqual(instruction["instruction"]["kind"], "revise_prose")
            status, result = self._request(base, "/api/dictation/proofread", {"mode": "prose", "text": original})
            self.assertEqual(status, 200)
            self.assertEqual(result["text"], revised)
            self.assertEqual(result["changed_parts"], 1)

    def test_dictation_review_returns_a_bounded_apa_proposal_and_note(self) -> None:
        original = "Rivas, Lucía. Leer en comunidad. Horizonte, 2024."
        proposed = "Rivas, L. (2024). Leer en comunidad. Horizonte."
        note = "El título del libro va en cursiva."
        provider = NullChatProvider(json.dumps({"text": proposed, "note": note}))
        assistant = DictationAssistant({"local": provider}, selected="local")
        body = {"text": original, "requests": ["Aplicá APA 7", "Dejá una nota breve"], "previous": proposed}
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), dictation_assistant=assistant)
            base = f"http://127.0.0.1:{server.server_port}"
            status, result = self._request(base, "/api/dictation/review", body)
            self.assertEqual(status, 200)
            self.assertEqual((result["text"], result["note"]), (proposed, note))
            prompt = json.loads(provider.calls[-1][0][-1]["content"])
            self.assertEqual(prompt["fragmento"], original)
            self.assertEqual(prompt["solicitudes"], body["requests"])
            self.assertEqual(prompt["propuesta_anterior"], proposed)

            for invalid, expected in (
                ({"text": "a" * 6_001}, 413),
                ({"requests": [7]}, 400),
                ({"previous": {"text": proposed}}, 400),
                ({"requests": []}, 400),
            ):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self._request(base, "/api/dictation/review", {**body, **invalid})
                with raised.exception as response:
                    self.assertEqual(response.code, expected)
                    result = json.load(response)
                self.assertNotIn("text", result)
            self.assertEqual(len(provider.calls), 1)

    def test_dictation_text_and_audio_routes_return_bounded_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assistant = DictationAssistant(
                {
                    "local": NullChatProvider(
                        '{"kind":"delete_from","text":"","target":"Buenos Aires",'
                        '"scope":"","number":0,"all_matches":false}'
                    )
                }
            )
            server = self._start(
                root,
                stt=NullSTTProvider("Lucy, borrá piedra y escribí agua"),
                dictation_assistant=assistant,
            )
            base = f"http://127.0.0.1:{server.server_address[1]}"

            status, assistant_status = self._request(base, "/api/dictation/assistant")
            self.assertEqual(status, 200)
            self.assertEqual(assistant_status["selected"], "rules")
            self.assertEqual(len(assistant_status["available"]), 2)
            self._request(base, "/api/dictation/assistant", {"provider": "local"})
            status, assisted = self._request(
                base,
                "/api/dictation/assist",
                {
                    "text": "Lucy, mejorá el final",
                    "draft": "Una tarde en Buenos Aires. Lo sé.",
                    "selection_start": 34,
                    "selection_end": 34,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(assisted["instruction"]["kind"], "delete_from")

            status, typed = self._request(
                base,
                "/api/dictation/interpret",
                {"text": "Léeme la última hoja", "commands_enabled": True},
            )
            self.assertEqual(status, 200)
            self.assertEqual(typed["instruction"]["scope"], "last_page")

            status, invoked = self._request(
                base,
                "/api/dictation/interpret",
                {
                    "text": "Lucy, borrá las últimas 20 palabras",
                    "commands_enabled": True,
                    "require_wake_word": True,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(invoked["instruction"]["kind"], "delete_last_words")
            self.assertEqual(invoked["instruction"]["number"], 20)

            exact_console_cases = (
                ("Lucy, borrar 10 palabras", "delete_last_words", 10, ""),
                (
                    "Lucy, Cambia las últimas 20 palabras por lo que vos quieras.",
                    "replace_last_words",
                    20,
                    "lo que vos quieras.",
                ),
                ("Lucy, borra desde y el signo en adelante", "delete_from", 0, ""),
                ("Lucy, Después de O, borrar todo.", "delete_from", 0, ""),
                (
                    "Lucy, O podría contener el universo, cambiarlo por 1 2 3 4 5",
                    "replace",
                    0,
                    "1 2 3 4 5",
                ),
                ("Lúci, después de O, borrar todo", "delete_from", 0, ""),
            )
            for transcript, kind, number, replacement in exact_console_cases:
                status, exact = self._request(
                    base,
                    "/api/dictation/interpret",
                    {
                        "text": transcript,
                        "commands_enabled": True,
                        "require_wake_word": True,
                    },
                )
                self.assertEqual(status, 200)
                self.assertEqual(exact["instruction"]["kind"], kind)
                self.assertEqual(exact["instruction"]["number"], number)
                self.assertEqual(exact["instruction"]["text"], replacement)
            self.assertEqual(exact["instruction"]["target"], "O")

            status, unknown = self._request(
                base,
                "/api/dictation/interpret",
                {
                    "text": "Lucy, inventá una edición desconocida",
                    "commands_enabled": True,
                    "require_wake_word": True,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(unknown["instruction"]["kind"], "noop")

            request = urllib.request.Request(
                base + "/api/dictation/transcribe?filename=dictation.webm&commands=1",
                data=b"synthetic-audio",
                headers={"Content-Type": "audio/webm"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5.0) as response:
                audio = json.loads(response.read())
            self.assertEqual(audio["instruction"]["kind"], "replace")
            self.assertEqual(audio["instruction"]["target"], "piedra")
            self.assertFalse(list(server.context.upload_root.glob("fusion_reader_upload_*")))

            status, speech = self._request(base, "/api/dictation/speak", {"text": "Texto seleccionado."})
            self.assertEqual(status, 200)
            self.assertTrue(speech["ok"])
            self.assertTrue(speech["audio_url"])
            self.assertEqual(self._request(base, speech["audio_url"])[0], 200)

    def test_quick_text_endpoint_is_transient_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = self._start(root)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            text = "Primero " + ("alpha " * 420) + "\n\nSegundo " + ("beta " * 420)

            status, loaded = self._request(
                base,
                "/api/quick-text",
                {"text": text, "start_offset": text.index("Segundo")},
            )
            self.assertEqual(status, 200)
            self.assertTrue(loaded["document"]["transient"])
            self.assertEqual(loaded["document"]["source_type"], "quick_text")
            self.assertEqual(loaded["quick_text"]["start_chunk"], 2)

            state = json.loads((root / "app" / "session_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["doc_id"], "")
            self.assertNotIn("text", state)

            oversized = "x" * (server.context.settings.limits.quick_text_max_chars + 1)
            request = urllib.request.Request(
                base + "/api/quick-text",
                data=json.dumps({"text": oversized}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5.0)
            self.assertEqual(raised.exception.code, 413)
            self.assertEqual(json.loads(raised.exception.read())["error"], "quick_text_too_large")

    def test_daily_api_route_matrix_and_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), synthetic_tts=True)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            for path in (
                "/health",
                "/api/status",
                "/api/build",
                "/api/library",
                "/api/voice/voices",
                "/api/voices",
                "/api/voice/metrics",
                "/api/voice/metrics/summary",
                "/api/voice/metrics/documents",
                "/api/voice/metrics/chunks?doc_id=matrix&limit=5",
                "/api/prepare/status",
                "/api/audio-export/status",
                "/api/references",
                "/api/notes?current_only=1&chunk_index=0",
                "/api/dialogue/status",
                "/api/media/status",
            ):
                with self.subTest(path=path):
                    self.assertEqual(self._request(base, path)[0], 200)

            for path, expected, content_type in (
                ("/", 200, "text/html; charset=utf-8"),
                ("/health", 200, "application/json"),
                ("/api/status", 200, "application/json"),
                ("/static/app.js", 200, "text/javascript; charset=utf-8"),
                ("/static/panda-fusion-emblem.webp", 200, "image/webp"),
                ("/static/missing.js", 404, None),
            ):
                request = urllib.request.Request(base + path, method="HEAD")
                try:
                    with urllib.request.urlopen(request, timeout=5.0) as response:
                        self.assertEqual(response.status, expected)
                        self.assertEqual(response.headers.get("Content-Type"), content_type)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, expected)

            text = "\n\n".join(f"Bloque {index}. " + ("texto " * 120) for index in range(1, 6))
            status, loaded = self._request(
                base,
                "/api/load",
                {"doc_id": "matrix", "title": "Matrix", "text": text},
            )
            self.assertEqual(status, 200)
            self.assertTrue(loaded["ok"])

            for path, payload in (
                ("/api/next", {}),
                ("/api/previous", {}),
                ("/api/jump", {"index": 2}),
                ("/api/reasoning/mode", {"mode": "thinking"}),
                ("/api/laboratory/mode", {"mode": "free"}),
                ("/api/profile", {"mode": "academica"}),
                ("/api/veil", {"mode": "lucy"}),
                ("/api/voice", {"voice": "female_03.wav"}),
                ("/api/dialogue/reset", {}),
                ("/api/laboratory/reset", {}),
                ("/api/chat/reset", {}),
            ):
                with self.subTest(path=path):
                    self.assertEqual(self._request(base, path, payload)[0], 200)

            _, read = self._request(base, "/api/read", {"play": False})
            self.assertTrue(read["ok"])
            self.assertTrue(read["audio_url"])
            self.assertEqual(self._request(base, read["audio_url"])[0], 200)
            request = urllib.request.Request(base + read["audio_url"], method="HEAD")
            with urllib.request.urlopen(request, timeout=5.0) as response:
                self.assertEqual(response.status, 200)

            self.assertEqual(self._request(base, "/api/voice/test", {"text": "Voz", "play": False})[0], 200)
            self.assertEqual(self._request(base, "/api/chat", {"message": "Resume el bloque"})[0], 200)
            dialogue = urllib.request.Request(
                base + "/api/dialogue/turn",
                data=b'{"text": "Resume"}',
                headers={"Content-Type": "Application/JSON; charset=UTF-8"},
            )
            with urllib.request.urlopen(dialogue, timeout=5.0) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response).get("transcript"), "Resume")

            _, note = self._request(base, "/api/notes/create", {"text": "Nota matrix", "chunk_index": 0})
            note_id = note["note"]["note_id"]
            doc_id = note["note"]["doc_id"]
            self._request(base, "/api/notes/update", {"note_id": note_id, "doc_id": doc_id, "text": "Editada"})
            self._request(base, "/api/notes/rename", {"note_id": note_id, "doc_id": doc_id, "label": "Clave"})
            self._request(base, "/api/notes/delete", {"note_id": note_id, "doc_id": doc_id})

            self._request(
                base,
                "/api/load",
                {"doc_id": "ref", "title": "Referencia", "text": "Texto de referencia", "role": "reference"},
            )
            self._request(base, "/api/reference/promote", {"doc_id": "ref"})
            self._request(base, "/api/reference/remove", {"doc_id": "matrix"})

            encoded = base64.b64encode(b"Texto importado").decode("ascii")
            self.assertEqual(
                self._request(
                    base,
                    "/api/import",
                    {"filename": "importado.txt", "mime": "text/plain", "data_b64": encoded},
                )[0],
                200,
            )

            self._request(base, "/api/load", {"doc_id": "export", "title": "Export", "text": text})
            self._request(base, "/api/prepare/start", {"start": "cursor"})
            self._request(base, "/api/prepare/cancel", {})
            _, export = self._request(base, "/api/audio-export", {"mode": "current"})
            job_id = export["job_id"]
            for _ in range(100):
                _, export_status = self._request(base, f"/api/audio-export/status/{job_id}")
                if export_status["state"] in {"done", "error", "cancelled"}:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(export_status["state"], "done")
            self.assertEqual(self._request(base, f"/api/audio-export/download/{job_id}")[0], 200)

            second = self._request(base, "/api/audio-export", {"mode": "full"})[1]
            self._request(base, f"/api/audio-export/cancel/{second['job_id']}", {})

            raw_request = urllib.request.Request(
                base + "/api/import-file?filename=raw.txt&mime=text/plain",
                data=b"Carga directa",
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            with urllib.request.urlopen(raw_request, timeout=5.0) as response:
                self.assertEqual(response.status, 200)

            async_request = urllib.request.Request(
                base + "/api/import-file/start?filename=async.txt&mime=text/plain",
                data=b"Carga asincrona",
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            with urllib.request.urlopen(async_request, timeout=5.0) as response:
                async_job = json.loads(response.read())
            for _ in range(100):
                try:
                    _, import_status = self._request(base, f"/api/import-status?id={async_job['job_id']}")
                except urllib.error.HTTPError:
                    continue
                if import_status["status"] in {"done", "error"}:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(import_status["status"], "done")

            pdf_job = JobStatus(job_id="pdf-status", filename="matrix.docx")
            server.context.pdf_jobs.add(pdf_job.job_id, pdf_job)
            self.assertEqual(self._request(base, "/api/tools/pdf-to-docx/status/pdf-status")[0], 200)
            self._request(base, "/api/tools/pdf-to-docx/cancel/pdf-status", {})

            docx = server.context.pdf_root / "download.docx"
            docx.parent.mkdir(parents=True, exist_ok=True)
            docx.write_bytes(b"PK synthetic docx")
            item = register_pdf_to_docx_download(
                server.context,
                docx,
                docx.name,
                ConversionResult(True, output_path=str(docx)),
            )
            self.assertEqual(self._request(base, f"/api/tools/pdf-to-docx/download/{item['id']}")[0], 200)

            self.assertEqual(self._request(base, "/api/document/clear", {})[0], 200)

    def test_pdf_multipart_upload_uses_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_address[1]}"
            boundary = "fusion-test-boundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="file"; filename="sample.pdf"\r\n'
                    "Content-Type: application/pdf\r\n\r\n"
                ).encode()
                + b"%PDF-1.4 synthetic\n"
                + f"\r\n--{boundary}--\r\n".encode()
            )

            def convert(_input: Path, output: Path, job: JobStatus):
                output.write_bytes(b"PK synthetic docx")
                job.state = "running"
                return ConversionResult(True, output_path=str(output), pages=1)

            request = urllib.request.Request(
                base + "/api/tools/pdf-to-docx",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with patch.object(tools_routes, "convert_pdf_to_docx", side_effect=convert):
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    created = json.loads(response.read())
                for _ in range(100):
                    _, status = self._request(base, f"/api/tools/pdf-to-docx/status/{created['job_id']}")
                    if status["state"] in {"done", "error"}:
                        break
                    threading.Event().wait(0.01)
            self.assertEqual(status["state"], "done")

    def test_media_multipart_transcription_downloads_and_mounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), synthetic_tts=True)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            preflight_status, preflight = self._request(
                base, "/api/media/capabilities?operation=transcribe&file_bytes=1024"
            )
            self.assertEqual(preflight_status, 200)
            self.assertTrue(preflight["ok"])
            audio = io.BytesIO()
            with wave.open(audio, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0\0" * 1600)
            boundary = "fusion-media-boundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="file"; filename="conference.wav"\r\n'
                    "Content-Type: audio/wav\r\n\r\n"
                ).encode()
                + audio.getvalue()
                + f"\r\n--{boundary}--\r\n".encode()
            )
            request = urllib.request.Request(
                base + "/api/media/transcribe",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5.0) as response:
                created = json.loads(response.read())
            for _ in range(200):
                _, status = self._request(base, f"/api/media/status/{created['job_id']}")
                if status["terminal"]:
                    break
                threading.Event().wait(0.02)
            self.assertEqual(status["state"], "done", status)
            self.assertEqual(self._request(base, status["output"]["pdf"]["download_url"])[0], 200)
            _, mounted = self._request(base, f"/api/media/mount/{created['job_id']}", {})
            self.assertTrue(mounted["mounted"])
            self.assertEqual(server.context.app.status()["document"]["source_type"], "media_transcript")

    def test_http_missing_resource_and_validation_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), synthetic_tts=True)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            for path in (
                "/missing",
                "/static/missing.js",
                "/api/import-status?id=missing",
                "/api/tools/pdf-to-docx/status/missing",
                "/api/tools/pdf-to-docx/download/missing",
                "/api/audio-export/status/missing",
                "/api/audio-export/download/missing",
                "/api/media/status/missing",
                "/api/media/download/missing/pdf",
                "/audio/missing.wav",
            ):
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
                    self._request(base, path)

            for path in ("/missing", "/static/missing.js", "/audio/missing.wav"):
                request = urllib.request.Request(base + path, method="HEAD")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5.0)
                self.assertEqual(raised.exception.code, 404)

            error_cases = (
                ("/api/load", {}, "missing_text_or_book_id"),
                ("/api/import", {}, "missing_file_data"),
                ("/api/import", {"filename": "bad.txt", "data_b64": "%%%"}, "invalid_base64"),
                ("/api/jump", {"index": "bad"}, "invalid_request"),
                ("/api/not-a-route", {}, "not_found"),
            )
            for path, payload, code in error_cases:
                with self.subTest(path=path, code=code), self.assertRaises(urllib.error.HTTPError) as raised:
                    self._request(base, path, payload)
                body = json.loads(raised.exception.read())
                self.assertEqual(body["error"], code)

            library = server.context.settings.paths.library
            library.mkdir(parents=True, exist_ok=True)
            (library / "book.txt").write_text("Texto de biblioteca", encoding="utf-8")
            self.assertEqual(self._request(base, "/api/load", {"book_id": "book.txt"})[0], 200)
            self.assertEqual(
                self._request(base, "/api/load", {"book_id": "book.txt", "role": "reference"})[0],
                200,
            )
            self.assertEqual(self._request(base, "/api/load", {"path": "book.txt"})[0], 200)
            self.assertEqual(
                self._request(base, "/api/load", {"path": "book.txt", "role": "reference"})[0],
                200,
            )
            encoded = base64.b64encode(b"Referencia importada").decode("ascii")
            self.assertEqual(
                self._request(
                    base,
                    "/api/import",
                    {"filename": "ref.txt", "data_b64": encoded, "role": "reference"},
                )[0],
                200,
            )

            missing_docx = server.context.pdf_root / "missing.docx"
            item = register_pdf_to_docx_download(
                server.context,
                missing_docx,
                missing_docx.name,
                ConversionResult(True, output_path=str(missing_docx)),
            )
            with self.assertRaises(urllib.error.HTTPError):
                self._request(base, f"/api/tools/pdf-to-docx/download/{item['id']}")

            boundary = "wrong-suffix"
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="sample.txt"\r\n'
                "Content-Type: text/plain\r\n\r\ntext\r\n"
                f"--{boundary}--\r\n"
            ).encode()
            request = urllib.request.Request(
                base + "/api/tools/pdf-to-docx",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5.0)
            self.assertEqual(raised.exception.code, 400)

            audio_request = urllib.request.Request(
                base + "/api/dialogue/turn?filename=turn.wav&chunk_index=0",
                data=b"RIFF synthetic",
                headers={"Content-Type": "audio/wav"},
                method="POST",
            )
            with urllib.request.urlopen(audio_request, timeout=5.0) as response:
                self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
