import unittest
import os
import tempfile
from pathlib import Path
from fusion_reader_v2 import ConversationCore, NullChatProvider
from tests.helpers import (
    test_app,
    make_reading_document,
    make_reading_sections,
)


class ConversationTests(unittest.TestCase):
    def test_reasoning_mode_defaults_to_thinking_when_env_is_not_forcing_normal(self):
        previous_mode = os.environ.get("FUSION_READER_REASONING_MODE")
        previous_think = os.environ.get("FUSION_READER_CHAT_THINK")
        try:
            os.environ.pop("FUSION_READER_REASONING_MODE", None)
            os.environ.pop("FUSION_READER_CHAT_THINK", None)
            app = test_app()
            self.assertEqual(app.reasoning_status()["mode"], "thinking")
        finally:
            if previous_mode is not None:
                os.environ["FUSION_READER_REASONING_MODE"] = previous_mode
            if previous_think is not None:
                os.environ["FUSION_READER_CHAT_THINK"] = previous_think

    def test_reasoning_mode_switch_persists_across_restart(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        changed = app.set_reasoning_mode("supreme")
        self.assertEqual(changed["mode"], "supreme")
        reopened = test_app(root=root)
        self.assertEqual(reopened.reasoning_status()["mode"], "supreme")

    def test_chat_uses_selected_reasoning_mode_settings(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.chat("¿Qué ves?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode"], "normal")
        self.assertEqual(out["reasoning_passes"], 1)
        self.assertFalse(chat_provider.calls[0][2]["think"])

    def test_normal_mode_chat_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("lectora crítica, rigurosa", prompt)

    def test_thinking_mode_chat_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("Leé con más calma", prompt)

    def test_free_laboratory_mode_chat_prompt_is_not_forced_back_to_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos de física cuántica.")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Estás en modo libre", prompt)
        self.assertIn("Los documentos son contexto opcional", prompt)

    def test_supreme_mode_keeps_free_laboratory_mode_in_final_pass(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Estás en modo libre", final_prompt)

    def test_contrapunto_mode_keeps_free_laboratory_mode_in_synthesis_pass(self):
        chat_provider = NullChatProvider("Síntesis final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("pensamiento_critico")
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        synthesis_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Estás en modo libre", synthesis_prompt)

    def test_supreme_mode_keeps_document_mode_in_final_pass(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.set_laboratory_mode("document")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertNotIn("Estás en modo libre", final_prompt)

    def test_contrapunto_mode_keeps_document_mode_in_synthesis_pass(self):
        chat_provider = NullChatProvider("Síntesis final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("pensamiento_critico")
        app.set_laboratory_mode("document")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos libremente.")
        synthesis_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertNotIn("Estás en modo libre", synthesis_prompt)

    def test_supreme_mode_chat_prompt_reuses_thinking_lucy_persona(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Lucy Cunningham", final_prompt)
        self.assertIn("depurá tus conceptos", final_prompt)

    def test_supreme_reasoning_runs_three_passes(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.chat("Pensá este fragmento con profundidad.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_passes"], 3)
        self.assertTrue(all(call[2]["think"] for call in chat_provider.calls))

    def test_reasoning_catalog_includes_pensamiento_critico(self):
        app = test_app()
        catalog = app.conversation.reasoning_catalog()
        modes = [item["mode"] for item in catalog]
        self.assertIn("pensamiento_critico", modes)

    def test_contrapunto_textual_runs_three_passes(self):
        chat_provider = NullChatProvider("Respuesta final dialéctica.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("contrapunto")
        app.load_text("doc", "Doc", "Contexto base.", prefetch=False)
        out = app.chat("Analizá este fragmento.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_passes"], 3)
        found_auditor = any(
            "Auditor" in m["content"] or "Critico" in m["content"]
            for call in chat_provider.calls
            for m in call[0]
            if m["role"] == "system"
        )
        self.assertTrue(found_auditor)

    def test_contrapunto_does_not_break_supreme(self):
        chat_provider = NullChatProvider("Respuesta final supreme.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Contexto.", prefetch=False)
        out = app.chat("Pensá profundo.")
        self.assertEqual(out["reasoning_mode"], "supreme")
        self.assertEqual(out["reasoning_passes"], 3)

    def test_contrapunto_synthesis_prompt_has_style_restrictions(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("pensamiento_critico")
        app.load_text("doc", "Doc", "Contexto.", prefetch=False)
        app.chat("Test")
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("EMPEZA DIRECTAMENTE", final_prompt)
        self.assertIn("NO USES ENCABEZADOS", final_prompt)

    def test_chat_gets_visible_chunk_and_full_document_without_tts(self):
        chat_provider = NullChatProvider("Veo el texto actual.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.\n\nContexto posterior.", prefetch=False)
        out = app.chat("¿Qué ves?")
        self.assertTrue(out["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("TEXTO EN PANTALLA:", prompt)
        self.assertIn("DOCUMENTO COMPLETO DISPONIBLE:", prompt)

    def test_chat_context_includes_reference_documents(self):
        chat_provider = NullChatProvider("Veo el apoyo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        app.chat("Compará.")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("DOCUMENTOS DE CONSULTA:", prompt)

    def test_chat_lists_all_reference_documents_even_if_first_is_long(self):
        chat_provider = NullChatProvider("Los veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref-1", "Análisis", " ".join(["analisis"] * 600))
        app.add_reference_text("ref-2", "desgrabaciones.docx", "Segunda.")
        app.chat("¿Ves los docs?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Análisis", prompt)
        self.assertIn("desgrabaciones.docx", prompt)

    def test_chat_navigation_focuses_reference_block_without_replacing_main(self):
        app = test_app()
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text("ref", "Desgrabaciones.docx", make_reading_sections(("C1", "P1"), ("C2", "P2")))
        out = app.chat("andá al bloque 2 de Desgrabaciones.docx")
        self.assertEqual(out["detail"], "focus_block")
        self.assertEqual(out["doc_id"], "ref")
        self.assertEqual(app.status()["doc_id"], "doc")
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)

    def test_chat_search_sets_laboratory_focus_on_match(self):
        app = test_app()
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text("ref", "D.docx", make_reading_sections(("C1", "P1"), ("C2", "YouTube"), ("C3", "C3")))
        out = app.chat("buscá YouTube en D.docx")
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(out["current"], app.laboratory_focus_status()["chunk_number"])
        self.assertEqual(app.laboratory_focus_status()["query"], "YouTube")

    def test_chat_combined_focus_and_search_prefers_search_result_when_both_are_requested(self):
        app = test_app()
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            make_reading_sections(
                ("Consulta uno", "Speaker 1. Nada de YouTube aca."),
                ("Consulta dos", "YouTube aparece fuerte en este bloque."),
                ("Consulta tres", "Mas texto."),
            ),
        )
        out = app.chat(
            "Andá al bloque 1 de Desgrabaciones.docx y buscá dónde habla de YouTube y ese bloque qué dice exactamente."
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(out["current"], app.laboratory_focus_status()["chunk_number"])
        self.assertIn("YouTube", out["answer"])

    def test_chat_search_is_accent_insensitive(self):
        app = test_app()
        app.load_text("doc", "Principal", "Fedro habla con Socrates.", prefetch=False)
        out = app.chat("buscá Sócrates")
        self.assertEqual(out["detail"], "search_matches")

    def test_followup_chat_gets_laboratory_focus_in_context(self):
        chat_provider = NullChatProvider("Sigo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Doc", prefetch=False)
        app.add_reference_text("ref", "D.docx", "YouTube")
        app.chat("buscá YouTube en D.docx")
        app.chat("¿qué plantea?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)

    def test_chat_compare_uses_focus_and_explicit_target(self):
        app = test_app()
        app.load_text(
            "doc",
            "Principal",
            make_reading_sections(
                ("Principal uno", "Bloque principal uno."),
                ("Principal dos", "Bloque principal dos importante."),
                paragraphs_per_section=10,
            ),
            prefetch=False,
        )
        app.add_reference_text(
            "ref",
            "Análisis Filosófico.docx",
            make_reading_sections(
                ("Consulta uno", "Bloque uno consulta."),
                ("Consulta dos", "Bloque dos consulta importante."),
                ("Consulta tres", "Bloque tres consulta."),
            ),
        )
        nav = app.chat("andá al bloque 2 de Análisis Filosófico.docx")
        self.assertTrue(nav["ok"])
        out = app.chat("compará este bloque con el bloque 2 del principal")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_compare")
        self.assertEqual(out["detail"], "compare_blocks")
        self.assertIn("Comparación:", out["answer"])
        self.assertIn("Análisis Filosófico.docx", out["answer"])
        self.assertIn("Principal", out["answer"])

    def test_chat_uses_recent_laboratory_text_without_document(self):
        chat_provider = NullChatProvider("Veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.chat("Pasted text.")
        app.chat("¿Ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[1][0])
        self.assertIn("MATERIAL RECIENTE DEL LABORATORIO:", prompt)

    def test_lucy_profiles_academica_and_bohemia(self):
        chat_provider = NullChatProvider("R.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.chat("Hola")
        self.assertIn("lectora crítica", chat_provider.calls[-1][0][0]["content"])
        app.set_profile("bohemia")
        app.chat("Hola")
        self.assertIn("Lucy Bohemia", chat_provider.calls[-1][0][0]["content"])

    def test_persona_overlay_length_and_independence(self):
        core = ConversationCore(NullChatProvider("."))
        overlay = core._persona_overlay(profile="academica", free_mode=False)
        self.assertLess(len(overlay), 3000)
        self.assertIn("anclada al documento", overlay)

    def test_veil_overlay_is_applied_to_prompt(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("nocturna")
        app.chat("H")
        self.assertIn("madrugada", chat_provider.calls[0][0][0]["content"])

    def test_directa_veil_is_sharp(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("directa")
        app.chat("H")
        self.assertIn("seco y frontal", chat_provider.calls[0][0][0]["content"])

    def test_desarme_veil_is_mechanical(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("desarme")
        app.chat("H")
        self.assertIn("mecanismo", chat_provider.calls[0][0][0]["content"])

    def test_lucy_veil_is_neutral(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("lucy")
        app.chat("H")
        self.assertNotIn("nocturna", chat_provider.calls[0][0][0]["content"])

    def test_bohemia_persona_contains_narrative_rein(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_profile("bohemia")
        app.chat("H")
        self.assertIn("intensidad en loop", chat_provider.calls[0][0][0]["content"])

    def test_free_mode_without_document_request_excludes_text(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Mancha", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Hola")
        self.assertNotIn("Mancha", chat_provider.calls[0][0][0]["content"])

    def test_free_mode_with_document_request_includes_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Según el documento, ¿dónde ocurre la acción?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Mancha", prompt)

    def test_document_mode_always_includes_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        app.set_laboratory_mode("document")
        app.chat("Hola")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Mancha", prompt)

    def test_document_mode_literal_request_injects_literal_instruction(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Text", prefetch=False)
        app.chat("¿Qué dice?")
        self.assertIn("respuesta literal", chat_provider.calls[0][0][0]["content"])

    def test_document_mode_interpretation_request_does_not_force_literal_instruction(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Text", prefetch=False)
        app.chat("¿Qué significa?")
        self.assertNotIn("respuesta literal", chat_provider.calls[0][0][0]["content"])

    def test_document_mode_mixed_literal_and_interpretation_request_orders_both(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Text", prefetch=False)
        app.chat("Leeme y explicame.")
        self.assertIn("Primero reproduce", chat_provider.calls[0][0][0]["content"])

    def test_free_mode_without_document_request_does_not_inject_literal_document_instruction(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Text", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Hola")
        self.assertNotIn("respuesta literal", chat_provider.calls[0][0][0]["content"])

    def test_free_mode_explicit_document_literal_request_can_inject_literal_instruction(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Text", prefetch=False)
        app.set_laboratory_mode("free")
        app.chat("Qué dice el texto?")
        self.assertIn("respuesta literal", chat_provider.calls[0][0][0]["content"])

    def test_supreme_mode_in_free_mode_honors_independence(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("d", "T", "Mancha", prefetch=False)
        app.set_laboratory_mode("free")
        app.set_reasoning_mode("supreme")
        app.chat("Tiempo.")
        self.assertNotIn("Mancha", chat_provider.calls[0][0][0]["content"])

    def test_closing_discipline_is_applied_by_default(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.chat("H")
        self.assertIn("No cierres por defecto con una pregunta", chat_provider.calls[0][0][0]["content"])

    def test_pregunta_viva_veil_omits_closing_discipline(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("pregunta_viva")
        app.chat("H")
        self.assertIn("Cerrá con una pregunta", chat_provider.calls[0][0][0]["content"])

    def test_debate_veil_is_not_forcing_question_as_routine(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_veil("debate")
        app.chat("H")
        self.assertIn("si hace falta, cerrá con una pregunta real", chat_provider.calls[0][0][0]["content"])

    def test_thinking_mode_does_not_force_questions(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.chat("H")
        self.assertIn("Abrí preguntas solo si son realmente necesarias", chat_provider.calls[0][0][0]["content"])
