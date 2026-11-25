import os
import unittest
from unittest.mock import patch

from fusion_reader_v2 import ConversationCore, NullChatProvider
from fusion_reader_v2.openclaw_bridge import NullExternalResearchBridge
from fusion_reader_v2.services.research import ResearchService
from tests.helpers import test_app, make_reading_sections


class ConversationSafetyTests(unittest.TestCase):
    def test_reflective_block_question_is_answered_without_moving_reader(self):
        app = test_app()
        provider = NullChatProvider("El bloque plantea una tensión filosófica.")
        app.conversation = ConversationCore(provider)
        app.set_reasoning_mode("normal")
        app.load_text(
            "d", "Libro", make_reading_sections(("Uno", "Materia."), ("Dos", "La conciencia.")), prefetch=False
        )
        before = app.session.status()["current"]
        for question in ("¿Qué significa el bloque 2?", "qué significa el bloque 2", "analizá el bloque 2"):
            with self.subTest(question=question):
                provider.calls.clear()
                out = app.chat(question)
                self.assertTrue(out["ok"])
                self.assertIn("tensión", out["answer"])
                self.assertEqual(len(provider.calls), 1)
                self.assertIn("La conciencia", str(provider.calls[0][0]))
                self.assertEqual(app.session.status()["current"], before)

    def test_questions_about_recent_speech_do_not_save_notes(self):
        for method in ("chat", "dialogue_turn_text"):
            with self.subTest(method=method):
                app = test_app()
                provider = NullChatProvider("Es una pregunta filosófica.")
                app.conversation = ConversationCore(provider)
                app.set_reasoning_mode("normal")
                out = getattr(app, method)("¿Lo que dije es coherente con Kant?")
                self.assertTrue(out["ok"])
                self.assertNotEqual(out["model"], "reader_notes")
                self.assertEqual(app.list_notes()["items"], [])
                self.assertTrue(provider.calls)

    def test_explicit_navigation_still_moves_but_voice_analysis_does_not(self):
        app = test_app()
        provider = NullChatProvider("El bloque plantea la conciencia.")
        app.conversation = ConversationCore(provider)
        app.set_reasoning_mode("normal")
        app.load_text("d", "Libro", make_reading_sections(("Uno", "Materia."), ("Dos", "Conciencia.")), prefetch=False)
        first = app.session.status()["current"]
        out = app.dialogue_turn_text("analizá el bloque 2")
        self.assertIn("conciencia", out["answer"])
        self.assertEqual(app.session.status()["current"], first)
        self.assertTrue(provider.calls)
        provider.calls.clear()
        out = app.chat("andá al bloque 2")
        self.assertTrue(out["ok"])
        self.assertEqual(app.session.status()["current"], 2)
        self.assertEqual(provider.calls, [])

    def test_negated_search_also_leaves_internal_focus_unchanged(self):
        app = test_app()
        app.load_text("d", "Libro", "Internet puede usarse para encontrar fuentes.", prefetch=False)
        before = app.laboratory_focus_status()
        for text in ("No buscar en internet", "Explicá la frase «busca en internet»"):
            with self.subTest(text=text):
                app.chat(text)
                self.assertEqual(app.laboratory_focus_status(), before)

    def test_negated_or_quoted_search_never_calls_bridge(self):
        for method in ("chat", "dialogue_turn_text"):
            for text in (
                "No buscar en internet",
                "Por favor no busques en internet sobre Kant",
                "Explicá la frase «busca en internet»",
                "¿Qué significa buscar en internet?",
                "No quiero que busques fuentes externas",
                "Buscá en el documento la frase «buscar en internet»",
                "Buscá en este documento la frase buscar en internet",
                "Buscá en el primer bloque la frase buscar en internet",
                "Buscá las fuentes en este PDF",
                "Revisá en mis notas las referencias",
                "Bien, pero ahora no busques información en internet.",
                "No, lo que te pedí es que con respecto al primer bloque del documento no busques en internet.",
                "Con respecto a cómo buscar información en internet, explicame el concepto.",
                "Sobre el ejemplo « buscá información en internet », ¿qué verbo se usa?",
                'Buscá la frase "papers sobre Kant"',
                "Buscá la antítesis",
            ):
                with self.subTest(method=method, text=text):
                    bridge = NullExternalResearchBridge()
                    app = test_app(external_research=bridge)
                    app.conversation = ConversationCore(NullChatProvider("Entendido."))
                    app.set_reasoning_mode("normal")
                    getattr(app, method)(text)
                    self.assertEqual(bridge.calls, [])

    def test_positive_search_requests_remain_explicit(self):
        service = ResearchService(NullExternalResearchBridge())
        for text in (
            "buscá en internet sobre Kant",
            "Por favor, buscá fuentes sobre Kant",
            "¿Podés buscar en internet sobre Hegel?",
            "quiero que investigues afuera sobre Hegel",
            "Buscá en internet las fuentes citadas en este PDF",
            "Buscá fuentes en la web",
            "Buscame una bibliografía externa sobre Kant",
        ):
            with self.subTest(text=text):
                self.assertTrue(service.is_explicit_request(text))

    def snapshot(self):
        return {
            "laboratory_mode": {"mode": "free"},
            "title": "TITULO_PRIVADO",
            "doc_id": "ID_PRIVADO",
            "current_chunk": "DOCUMENTO_PRIVADO",
            "document_text": "DOCUMENTO_PRIVADO",
            "notes": [{"text": "NOTA_PRIVADA"}],
            "laboratory_focus": {"title": "FOCO_TITULO", "text": "FOCO_PRIVADO"},
            "reference_documents": [{"doc_id": "REF_PRIVADA", "title": "REF_TITULO"}],
        }

    def test_free_mode_context_is_explicit_in_text_and_voice(self):
        core = ConversationCore(NullChatProvider())
        for dialogue in (False, True):
            for question, notes, focus in (
                ("Hablemos de Kant", False, False),
                ("Analizá mis notas", True, False),
                ("Mostrame las notas", True, False),
                ("Analizá el foco del laboratorio", False, True),
                ("¿Para qué sirve tomar notas?", False, False),
                ("¿Cómo se define un foco?", False, False),
                ("No analices mis notas", False, False),
                ("No uses mis notas ni el foco del laboratorio", False, False),
            ):
                with self.subTest(dialogue=dialogue, question=question):
                    messages = core._messages(question, self.snapshot(), dialogue=dialogue)
                    context = messages[1]["content"]
                    self.assertEqual("NOTA_PRIVADA" in context, notes)
                    self.assertEqual("FOCO_PRIVADO" in context, focus)
                    for private in ("TITULO_PRIVADO", "ID_PRIVADO", "DOCUMENTO_PRIVADO", "REF_TITULO", "REF_PRIVADA"):
                        self.assertNotIn(private, context)

    def test_free_mode_document_request_keeps_notes_separate(self):
        core = ConversationCore(NullChatProvider())
        context = core._messages("Analizá el documento", self.snapshot())[1]["content"]
        self.assertIn("DOCUMENTO_PRIVADO", context)
        self.assertNotIn("NOTA_PRIVADA", context)
        self.assertNotIn("FOCO_PRIVADO", context)

    def test_retrieved_excerpt_contains_late_match_with_original_accents(self):
        core = ConversationCore(NullChatProvider())
        record = {
            "title": "Fuente",
            "role": "reference",
            "total": 1,
            "chunks": [{"chunk_number": 1, "text": "Introducción. " * 200 + "La autonomía crítica es central."}],
        }
        excerpt = core._render_document_excerpt(record, "autonomía crítica", [])
        self.assertIn("La autonomía crítica es central.", excerpt)
        self.assertIn("recortado", excerpt)

    def test_context_has_one_common_budget_in_every_reasoning_pass(self):
        with patch.dict(os.environ, {"FUSION_READER_CHAT_MAX_INPUT_CHARS": "16000"}):
            for mode in ("normal", "supreme", "pensamiento_critico"):
                with self.subTest(mode=mode):
                    provider = NullChatProvider("Respuesta " * 4000)
                    core = ConversationCore(provider)
                    snapshot = {
                        "current_chunk": "VISIBLE " * 20000,
                        "document_text": "DOCUMENTO " * 20000,
                        "notes": [{"text": "NOTA " * 10000}],
                        "title": "Fuente",
                    }
                    history = [{"role": "user", "content": "HISTORIA " * 20000}]
                    question = "PREGUNTA_ACTUAL: explicá la relación entre conciencia y materia."
                    result = core.ask(question, snapshot, history=history, reasoning_mode=mode)
                    self.assertTrue(result.ok)
                    for messages, _, _ in provider.calls:
                        self.assertLessEqual(sum(len(item["content"]) for item in messages), 16000)
                        self.assertIn(question, str(messages))
                        self.assertEqual(str(messages).count(question), 1)
                    self.assertEqual(
                        provider.calls[0][0][0], core._messages(question, snapshot, history, reasoning_mode=mode)[0]
                    )

    def test_search_focus_excerpt_contains_late_match_in_free_mode(self):
        core = ConversationCore(NullChatProvider())
        snapshot = self.snapshot()
        snapshot["laboratory_focus"] = {
            "title": "Fuente",
            "query": "autonomía",
            "text": "Introducción. " * 200 + "La autonomía crítica.",
        }
        context = core._messages("Analizá el foco del laboratorio", snapshot)[1]["content"]
        self.assertIn("La autonomía crítica.", context)
        self.assertNotIn("DOCUMENTO_PRIVADO", context)

    def test_recent_history_is_not_duplicated(self):
        core = ConversationCore(NullChatProvider())
        history = [{"role": "user", "content": "MARCADOR_UNICO_DEL_USUARIO"}]
        for dialogue in (False, True):
            with self.subTest(dialogue=dialogue):
                messages = core._messages("Continuá", {"laboratory_history": history}, history, dialogue=dialogue)
                self.assertEqual(str(messages).count("MARCADOR_UNICO_DEL_USUARIO"), 1)

    def test_critical_mode_environment_and_legacy_alias_are_honored(self):
        for mode in ("pensamiento_critico", "contrapunto"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"FUSION_READER_REASONING_MODE": mode}):
                core = ConversationCore(NullChatProvider())
                self.assertEqual(core.default_reasoning_mode, "pensamiento_critico")
