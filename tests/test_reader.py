import unittest
import time
import os
import tempfile
from pathlib import Path
from concurrent.futures import Future
from fusion_reader_v2 import split_text
from fusion_reader_v2.reader import Document
from tests.helpers import (
    test_app,
    NullTTSProvider,
    FailingTTSProvider,
    make_reading_document,
    make_reading_sections,
    manual_document,
)

class ReaderTests(unittest.TestCase):
    def test_split_text_packs_short_paragraphs_into_page_sized_chunks(self):
        paragraph = (
            "La realidad parece una costumbre compartida, pero cada lectura la fuerza a declararse de nuevo "
            "ante la conciencia y deja una huella breve pero suficiente para sostener el siguiente pasaje."
        )
        text = "\n\n".join(paragraph for _ in range(20))
        chunks = split_text(text)
        self.assertLess(len(chunks), 20)
        self.assertTrue(all(len(chunk) >= 1200 for chunk in chunks[:-1]))

    def test_split_text_joins_heading_with_following_content(self):
        paragraph = (
            "La realidad parece una costumbre compartida, pero cada lectura la fuerza a declararse de nuevo "
            "ante la conciencia. Esa torsion pequena vuelve visible lo que antes pasaba por obvio. "
        )
        text = f"Capitulo 1\n\nIntroduccion\n\n{paragraph * 12}"
        chunks = split_text(text)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("Capitulo 1", chunks[0])
        self.assertIn("Introduccion", chunks[0])
        self.assertNotEqual(chunks[0].strip(), "Capitulo 1")
        self.assertNotIn("Capitulo 1", chunks[1:] if len(chunks) > 1 else [])

    def test_split_text_breaks_long_sentence_for_faster_tts(self):
        text = " ".join(f"palabra{i}" for i in range(120))
        chunks = split_text(text, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))

    def test_default_chunks_keep_a_page_sized_reading_range(self):
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda. Cuando un texto academico se despliega con paciencia, cada argumento "
            "solicita una lectura continua capaz de sostener relaciones lejanas sin perder el hilo. "
        )
        chunks = split_text("\n\n".join(paragraph for _ in range(12)))
        self.assertTrue(all(len(chunk) <= 3200 for chunk in chunks))
        self.assertTrue(all(len(chunk) >= 1200 for chunk in chunks[:-1]))

    def test_split_text_skips_pdf_zero_noise(self):
        paragraph = (
            "Una frase suficientemente larga para demostrar que el ruido cero debe desaparecer mientras el texto "
            "real se mantiene unido al resto del contenido legible del documento."
        )
        chunks = split_text(f"{paragraph}\n\n0\n\n{paragraph}\n\n{paragraph}")
        self.assertEqual(len(chunks), 1)
        self.assertNotIn("\n\n0\n\n", chunks[0])

    def test_split_text_avoids_one_word_chunks(self):
        paragraph = (
            "La lectura continua necesita contexto suficiente para que una linea breve no quede aislada "
            "como si fuera un bloque completo de navegacion."
        )
        text = "Uno.\n\nDos.\n\nTres.\n\n" + "\n\n".join(paragraph for _ in range(10))
        chunks = split_text(text)
        stripped = [chunk.strip() for chunk in chunks]
        self.assertNotIn("Uno.", stripped)
        self.assertNotIn("Dos.", stripped)
        self.assertNotIn("Tres.", stripped)

    def test_split_text_splits_very_long_paragraph_by_sentence(self):
        sentence = (
            "Esta oracion extensa recorre matices, agrega ejemplos, enlaza autores y vuelve sobre una hipotesis "
            "anterior para observar como la lectura continua necesita sostener una corriente semantica sin un corte "
            "brusco que fracture la percepcion del argumento. "
        )
        text = (sentence * 40).strip()
        chunks = split_text(text)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 3200 for chunk in chunks))
        self.assertTrue(all(" " in chunk for chunk in chunks))

    def test_document_from_text_uses_page_sized_chunks_by_default(self):
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        document = Document.from_text("doc", "Doc", "\n\n".join(paragraph for _ in range(30)))
        self.assertGreater(len(document.chunks), 1)
        self.assertTrue(all(len(chunk) <= 3200 for chunk in document.chunks))
        self.assertTrue(all(len(chunk) >= 1200 for chunk in document.chunks[:-1]))

    def test_split_text_respects_explicit_max_chars_for_compatibility(self):
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        chunks = split_text("\n\n".join(paragraph for _ in range(8)), max_chars=420)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 420 for chunk in chunks))

    def test_reader_load_and_navigation(self):
        app = test_app()
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        app.load_text("doc", "Doc", "\n\n".join(f"{paragraph}{i}." for i in range(40)))
        self.assertEqual(app.status()["current"], 1)
        first = app.status()["text"]
        second = app.next()["text"]
        self.assertNotEqual(first, second)
        self.assertEqual(app.previous()["text"], first)
        jumped = app.jump(3)["text"]
        self.assertEqual(app.status()["current"], 3)
        self.assertEqual(app.status()["text"], jumped)

    def test_reader_session_navigation_still_reports_current_total(self):
        app = test_app()
        paragraph = (
            "La tradicion filosofica no transmite solo conceptos sino ritmos de atencion, formas de insistencia "
            "y modos de ordenar la duda dentro de una secuencia larga y continua de lectura. "
        )
        app.load_text("doc", "Doc", "\n\n".join(paragraph for _ in range(40)))
        initial = app.status()
        self.assertGreater(initial["total"], 2)
        self.assertEqual(initial["current"], 1)
        app.next()
        self.assertEqual(app.status()["current"], 2)
        app.jump(app.status()["total"])
        self.assertEqual(app.status()["current"], app.status()["total"])
        app.previous()
        self.assertEqual(app.status()["current"], app.status()["total"] - 1)

    def test_short_document_remains_single_chunk(self):
        chunks = split_text("Documento corto con una sola idea y un cierre breve.")
        self.assertEqual(len(chunks), 1)

    def test_read_current_prefetches_next(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", make_reading_document("Doc", 24))
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertIn("ready_ms", out)
        self.assertIn("synthesis_ms", out)
        status = app.status()
        self.assertEqual(status["prefetch_index"], 1)

    def test_read_current_reuses_current_prefetch(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.")
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertEqual(len(provider.calls), 1)

    def test_prepare_document_caches_all_chunks_in_background(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", make_reading_document("Doc", 36), prefetch=False)
        started = app.prepare_document()
        self.assertEqual(started["status"], "running")
        for _ in range(50):
            status = app.prepare_status()
            if status["status"] == "done":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "done")
        self.assertGreater(app.prepare_status()["generated"], 1)
        self.assertEqual(app.prepare_status()["generated"], app.prepare_status()["total"])
        before = len(provider.calls)
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertTrue(out["cached"])
        self.assertEqual(len(provider.calls), before)

    def test_load_text_resets_previous_prepare_status(self):
        app = test_app()
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        app.prepare_document()
        for _ in range(50):
            if app.prepare_status()["status"] == "done":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "done")
        app.load_text("new", "Nuevo", "Dos.", prefetch=False)
        self.assertEqual(app.prepare_status()["status"], "idle")
        self.assertEqual(app.prepare_status()["doc_id"], "")

    def test_read_current_times_out_stale_prefetch(self):
        app = test_app()
        app.prefetch_wait_seconds = 0.001
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        stale = Future()
        key = app._prefetch_key(
            app._document_generation, 0, "Uno.", app.voice.voice, app.voice.language
        )
        with app._prefetch_lock:
            app._prefetch_futures[key] = stale
            app._prefetch_index = 0
            app._prefetch_future = stale
        out = app.read_current(play=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["detail"], "prefetch_timeout")

    def test_reference_documents_can_be_added_without_replacing_main(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.\n\nSegundo bloque.", prefetch=False)
        out = app.add_reference_text("ref", "Consulta", "Texto de consulta.\n\nOtro dato.")
        self.assertTrue(out["ok"])
        status = app.status()
        self.assertEqual(status["doc_id"], "doc")
        self.assertEqual(status["main_document"]["title"], "Principal")
        self.assertEqual(len(status["reference_documents"]), 1)
        self.assertEqual(status["reference_documents"][0]["doc_id"], "ref")

    def test_prepare_document_reports_error_when_tts_is_unavailable(self):
        app = test_app(tts=FailingTTSProvider())
        app.session.load(manual_document("doc", "Doc", ["Uno.", "Dos."]))
        started = app.prepare_document()
        self.assertEqual(started["status"], "running")
        for _ in range(50):
            status = app.prepare_status()
            if status["status"] == "error":
                break
            time.sleep(0.01)
        status = app.prepare_status()
        self.assertEqual(status["status"], "error")
        self.assertIn("voz no está disponible", status["message"])
        self.assertEqual(status["failed"], 2)

    def test_read_current_returns_friendly_tts_error(self):
        app = test_app(tts=FailingTTSProvider())
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        out = app.read_current(play=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["detail"], "tts_down")
        self.assertIn("voz no está disponible", out["error"])

    def test_promote_reference_swaps_main_and_previous_main_becomes_reference(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        out = app.promote_reference_document("ref", prefetch=False)
        self.assertTrue(out["ok"])
        status = app.status()
        self.assertEqual(status["doc_id"], "ref")
        self.assertEqual(status["title"], "Consulta")
        refs = status["reference_documents"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["doc_id"], "doc")

    def test_restart_restores_reference_documents(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        reopened = test_app(root=root)
        status = reopened.status()
        self.assertEqual(status["doc_id"], "doc")
        self.assertEqual(len(status["reference_documents"]), 1)
        self.assertEqual(status["reference_documents"][0]["title"], "Consulta")

    def test_clear_document_resets_state(self):
        app = test_app()
        app.load_text("doc123", "El Quijote", "En un lugar de la Mancha...", prefetch=False)
        self.assertEqual(app.session.status()["title"], "El Quijote")
        status = app.clear_document()
        self.assertEqual(status["title"], "")
        self.assertEqual(status["total"], 0)
        self.assertEqual(status["doc_id"], "")
        self.assertEqual(app.session.document, None)

    def test_status_free_mode_with_document_loaded_exposes_anchor_without_document_use(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.set_laboratory_mode("free")
        status = app.status()
        self.assertTrue(status["document"]["loaded"])
        self.assertEqual(status["anchor"]["mode"], "free")
        self.assertFalse(status["anchor"]["uses_document"])
        self.assertTrue(status["anchor"]["document_available"])

    def test_status_document_mode_with_document_loaded_exposes_anchor_use(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.set_laboratory_mode("document")
        status = app.status()
        self.assertTrue(status["document"]["loaded"])
        self.assertEqual(status["anchor"]["mode"], "document")
        self.assertTrue(status["anchor"]["uses_document"])
        self.assertTrue(status["anchor"]["document_available"])

    def test_status_after_clear_document_exposes_no_document_available(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.set_laboratory_mode("free")
        app.clear_document()
        status = app.status()
        self.assertFalse(status["document"]["loaded"])
        self.assertFalse(status["anchor"]["document_available"])
        self.assertFalse(status["anchor"]["uses_document"])

from tests.helpers import attach_legacy_tests

attach_legacy_tests(ReaderTests, (
    "test_restart_restores_last_document_cursor_and_notes",
))
