from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import nullcontext

from PIL import Image

from fusion_reader_v2 import documents
from fusion_reader_v2.md_to_docx import repair_glued_words_v4
from fusion_reader_v2.reader import chunk_index_for_offset, split_text
from fusion_reader_v2.transcript_correction import validate_conservative_correction
from fusion_reader_v2.transcript_correction import OllamaTranscriptCorrector
from fusion_reader_v2.web.routes.documents import load_imported_document
from tests.helpers import managed_test_app


class TextFidelityTests(unittest.TestCase):
    def test_valid_docx_directory_entries_are_not_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("word/", b"")
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Filosofía crítica</w:t></w:r></w:p></w:document>'.encode(),
            )
        self.assertEqual(documents.docx_to_text(output.getvalue()), "Filosofía crítica")

    def test_rtf_hex_escapes_preserve_spanish(self):
        value = r"{\rtf1\ansi\ansicpg1252 Filosof\'eda y cr\'edtica.}"
        result = documents.import_document_bytes("test.rtf", value.encode())
        self.assertEqual(result.text, "Filosofía y crítica.")

    def test_rtf_unicode_fallback_and_destination_groups(self):
        value = r"{\rtf1\ansi\uc1{\fonttbl{\f0 Arial;}}{\*\generator No leer;}Filosof\u237?a y \uc0 cr\u237tica.\par Cita: \{ser\} \\ fin.}"
        text = documents.normalize_text(documents.rtf_to_text(value))
        self.assertEqual(text, "Filosofía y crítica.\nCita: {ser} \\ fin.")

    def test_rtf_surrogate_pairs_and_codepage(self):
        emoji = documents.normalize_text(documents.rtf_to_text(r"{\rtf1\ansi\uc1 Signo \u-10179?\u-8704?.}"))
        self.assertEqual(emoji, "Signo 😀.")
        cyrillic = documents.normalize_text(documents.rtf_to_text(r"{\rtf1\ansi\ansicpg1251 \'cf\'f0\'e8\'e2\'e5\'f2}"))
        self.assertEqual(cyrillic, "Привет")

    def test_single_long_token_respects_hard_limit_without_losing_characters(self):
        text = "á" * 501
        chunks = split_text(text, max_chars=120)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_cursor_mapping_uses_source_ranges_when_noise_was_removed(self):
        first = "primera " * 180
        second = "segunda " * 180
        text = first + "\r\n\r\n0\r\n\r\n" + second
        chunks = split_text(text, max_chars=1800)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunk_index_for_offset(text, chunks, text.index("segunda")), 1)
        self.assertEqual(chunk_index_for_offset(text, chunks, text.index("segunda") - 1), 1)

    def test_browser_utf16_offset_starts_the_correct_chunk(self):
        first, second, third = "😀" * 2000, "B" * 1300, "C" * 2100
        text = "\n\n".join((first, second, third))
        offset = len((first + "\n\n").encode("utf-16-le")) // 2
        with managed_test_app() as app:
            status = app.load_quick_text(text, start_offset=offset, prefetch=False)
            self.assertEqual(status["current"], 2)
            self.assertEqual(status["text"], second)
            self.assertEqual(app.session.document.text, text)

    def test_editorial_cleanup_preserves_valid_spanish_vocabulary(self):
        for word in ["enlace", "delantal", "enlatado", "enlazado", "enlazamiento", "delantero"]:
            with self.subTest(word=word):
                self.assertEqual(repair_glued_words_v4(word)[0], word)
        self.assertIn("Diario de", repair_glued_words_v4("Diariode Antoninus")[0])

    def test_correction_rejects_negation_changes(self):
        for original, candidate in [
            ("La sustancia no cambia durante el tiempo.", "La sustancia cambia durante el tiempo."),
            ("La tesis es posible sin esa premisa.", "La tesis es posible con esa premisa."),
            ("Nunca afirma esa conclusión.", "Siempre afirma esa conclusión."),
        ]:
            with self.subTest(original=original):
                self.assertFalse(validate_conservative_correction(original, candidate)[0])

    def test_correction_preserves_quoted_text(self):
        for quote, replacement in [
            ("«verdadero»", "«falso»"),
            ('"el ser existe"', '"el ser persiste"'),
            ("“libertad”", "“necesidad”"),
            ("'el ser existe'", "'el ser persiste'"),
        ]:
            original = f"El autor escribe {quote} en este pasaje de la obra."
            candidate = original.replace(quote, replacement)
            with self.subTest(quote=quote):
                self.assertFalse(validate_conservative_correction(original, candidate)[0])

    def test_correction_preserves_numbers_dates_and_number_words(self):
        for old, new in [
            ("1781", "1787"),
            ("-5", "5"),
            ("12,5", "12,6"),
            ("dos", "tres"),
            ("veintidós", "veintitrés"),
            ("II", "III"),
        ]:
            original = f"El texto presenta {old} como dato de la exposición."
            with self.subTest(old=old):
                self.assertFalse(validate_conservative_correction(original, original.replace(old, new))[0])
        self.assertFalse(
            validate_conservative_correction("Se indican dos casos y 3 pruebas.", "Se indican 3 casos y dos pruebas.")[
                0
            ]
        )
        self.assertFalse(
            validate_conservative_correction(
                "Se conserva la referencia B12 del texto.", "Se conserva la referencia B13 del texto."
            )[0]
        )

    def test_negation_accent_and_punctuation_can_be_corrected_without_changing_polarity(self):
        self.assertTrue(validate_conservative_correction("jamas dijo eso.", "Jamás dijo eso.")[0])
        self.assertTrue(validate_conservative_correction("No afirmó eso.", "No afirmó eso!")[0])

    def test_new_or_unbalanced_quotes_are_not_accepted(self):
        original = "El texto expone el ser en este párrafo."
        self.assertFalse(validate_conservative_correction(original, original.replace("el ser", '"el ser'))[0])

    def test_full_page_ocr_does_not_drop_upper_body_or_duplicate_columns(self):
        text = "Este cuerpo superior conserva la tesis del autor.\nLa filosofía requiere crítica y discusión.\n\nEl segundo párrafo desarrolla el argumento sin omisiones."
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page.png"
            Image.new("RGB", (800, 1200), "white").save(image)
            with patch.object(documents, "run_tesseract_plain", return_value=text) as run:
                result = documents.structured_ocr_image(image, "synthetic-tesseract")
        self.assertEqual(result.count("Este cuerpo superior"), 1)
        self.assertEqual(result, text)
        self.assertEqual(run.call_count, 1)

    def test_raw_extraction_remains_recoverable(self):
        raw = "Fuente reconocida &amp; literal.\n\nSegunda línea.\n"
        result = documents.imported("scan.pdf", "Fuente limpia.", "pdf", "fixture", raw_text=raw)
        self.assertEqual(result.raw_text, raw)

    def test_raw_ocr_file_is_saved_without_editorial_changes(self):
        raw = "Fuente reconocida &amp; literal.\n\nSegunda línea.\n"
        imported = documents.imported("scan.pdf", "Vista limpia.", "pdf", "fixture", raw_text=raw)
        with tempfile.TemporaryDirectory() as tmp, managed_test_app() as app:
            context = SimpleNamespace(
                app=app, converted_root=Path(tmp), import_publication=lambda *_args: nullcontext()
            )
            result = load_imported_document(context, imported)
            self.assertEqual(Path(result["raw_text"]).read_text(), raw)

    def test_protected_correction_is_a_safe_rejection_in_the_dictation_pipeline(self):
        original = "La sustancia no cambia durante el tiempo."
        candidate = "La sustancia cambia durante el tiempo."
        payloads = [{"models": [{"name": "synthetic"}]}, {"message": {"content": candidate}}]
        with managed_test_app() as app:
            app._dictation_corrector = OllamaTranscriptCorrector(model="synthetic")
            with patch(
                "urllib.request.urlopen", side_effect=[io.BytesIO(json.dumps(item).encode()) for item in payloads]
            ):
                result = app.dictation_proofread(original)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], original)
        self.assertEqual(result["rejected_parts"], 1)
