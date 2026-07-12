from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
import subprocess

from fusion_reader_v2 import documents
from fusion_reader_v2 import md_to_docx as markdown
from fusion_reader_v2 import pdf_to_docx as pdf


class DocumentTextProcessingMatrixTests(unittest.TestCase):
    def test_filename_mime_encoding_and_markup_boundaries(self) -> None:
        self.assertEqual(documents.safe_filename("../../ raro?.txt"), "raro?.txt")
        self.assertEqual(documents.doc_id_for_filename("Libro.txt"), "Libro")
        for mime, suffix in (
            ("text/plain", ".txt"),
            ("text/markdown", ".md"),
            ("application/pdf", ".pdf"),
            ("application/vnd.oasis.opendocument.text", ".odt"),
            ("unknown/type", ""),
        ):
            self.assertEqual(documents.suffix_from_mime(mime), suffix)
        self.assertEqual(documents.decode_text("á".encode()), "á")
        self.assertEqual(documents.decode_text(b"ol\xe1"), "olá")
        self.assertTrue(documents.looks_like_text(b"plain readable text"))
        self.assertFalse(documents.looks_like_text(b"\x00\x01\x02"))
        self.assertEqual(documents.normalize_text(" a\r\n\r\n\r\n b "), "a\n\n b")
        self.assertIn("Título", documents.html_to_text("<h1>Título</h1><p>Texto &amp; más</p>"))
        self.assertIn("negrita", documents.rtf_to_text(r"{\rtf1 Texto \b negrita\b0\par fin}"))

    def test_docx_odt_and_path_import_fail_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docx = root / "sample.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:p><w:r><w:t>Texto DOCX</w:t></w:r></w:p></w:document>",
                )
            self.assertIn("Texto DOCX", documents.docx_to_text(docx.read_bytes()))

            odt = root / "sample.odt"
            with zipfile.ZipFile(odt, "w") as archive:
                archive.writestr(
                    "content.xml",
                    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                    "<text:p>Texto ODT</text:p></office:document-content>",
                )
            self.assertIn("Texto ODT", documents.odt_to_text(odt.read_bytes()))

            raw = root / "extensionless"
            raw.write_text("Texto sin extensión", encoding="utf-8")
            imported = documents.import_document_path(raw.name, raw)
            self.assertIn("sin extensión", imported.text)
            with mock.patch("fusion_reader_v2.documents.shutil.which", return_value=None):
                with self.assertRaises(ValueError):
                    documents.import_document_bytes("bad.bin", b"\x00\x01")

    def test_pdf_cleanup_ocr_signal_and_heading_matrix(self) -> None:
        marked = documents.mark_pdf_pages("uno\f\f tres", clean=False)
        self.assertIn("[Pagina 1]", marked)
        self.assertIn("[Pagina 3]", marked)
        cleaned = documents.clean_pdf_text(
            "REVISTA 2024\n1\nEsto es una ora-\nción completa que sigue\nen la línea siguiente.\n\nCAPÍTULO IV\nTexto final."
        )
        self.assertIn("oración", cleaned)
        self.assertIn("REVISTA 2024", cleaned)
        self.assertGreater(documents.meaningful_chars(cleaned), 10)

        for value, expected in (
            ("123 456", True),
            ("palabras normales", False),
            ("I II III IV", False),
        ):
            self.assertEqual(documents.looks_like_numeric_artifact(value), expected)
        self.assertTrue(
            documents.enough_plain_page_signal(
                "Esta es una página con texto español suficiente para leer.\n"
                "La segunda línea contiene varias oraciones claras para una lectura normal.\n"
                "En la tercera línea hay palabras de uso común y una señal documental estable."
            )
        )
        self.assertFalse(documents.enough_plain_page_signal("1 2 3"))
        self.assertIsInstance(documents.looks_like_noisy_index_page("1 ..... 22\n2 ..... 30"), bool)
        self.assertGreater(documents.stopword_ratio("de la casa y el libro para una lectura"), 0.2)

        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t10\t40\t12\t95\tCAPÍTULO\n"
            "5\t1\t1\t1\t1\t2\t55\t10\t20\t12\t90\tUNO\n"
            "5\t1\t1\t2\t1\t1\t10\t40\t40\t12\t88\tTexto\n"
        )
        lines = documents.ocr_lines_from_tsv(tsv)
        self.assertTrue(lines)
        self.assertIn("CAPÍTULO", documents.format_ocr_lines(lines))
        self.assertIsInstance(documents.structured_ocr_page(tsv), str)
        for word in ("palabra", "á", "123", "@@@", "I"):
            self.assertIsInstance(documents.keep_ocr_word(word), bool)
        for line, confidence in (("Texto válido de lectura", 90.0), ("123", 10.0), ("CAPÍTULO I", 40.0)):
            self.assertIsInstance(documents.keep_ocr_line(line, confidence), bool)
        self.assertIn("hola", documents.repair_ocr_spacing("hola,mundo").lower())
        self.assertEqual(documents.clean_heading("CAPÍTULO IV: EL VIAJE"), "Capítulo IV")
        self.assertEqual(documents.heading_level("CAPÍTULO IV"), "#")

    def test_document_import_pdf_ocr_and_office_external_boundaries(self) -> None:
        events: list[tuple] = []
        documents.report_progress(lambda *args: events.append(args), "stage", 1, 2, "message")
        documents.report_progress(lambda *_args: (_ for _ in ()).throw(RuntimeError("ignored")), "stage")
        self.assertEqual(events[0][0], "stage")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            html_path = root / "sample.html"
            html_path.write_text("<p>Texto HTML</p>", encoding="utf-8")
            self.assertEqual(documents.import_document_path(html_path.name, html_path).source_type, "html")
            rtf_path = root / "sample.rtf"
            rtf_path.write_text(r"{\rtf1 Texto RTF}", encoding="utf-8")
            self.assertEqual(documents.import_document_path(rtf_path.name, rtf_path).source_type, "rtf")
            unknown = root / "sample.xyz"
            unknown.write_text("Texto detectado por contenido", encoding="utf-8")
            self.assertEqual(documents.import_document_path(unknown.name, unknown).source_type, "text")
            no_suffix = root / "mime-file"
            no_suffix.write_text("Texto con mime", encoding="utf-8")
            self.assertTrue(
                documents.import_document_path(no_suffix.name, no_suffix, mime="text/plain").title.endswith(".txt")
            )

            pdf_path = root / "sample.pdf"
            pdf_path.write_bytes(b"%PDF")
            with mock.patch.object(documents.shutil, "which", return_value=None):
                with self.assertRaisesRegex(ValueError, "pdftotext_not_found"):
                    documents.pdf_to_text(pdf_path.name, pdf_path)

            failed = subprocess.CompletedProcess([], 1, stdout="", stderr="pdf failed")
            with (
                mock.patch.object(documents.shutil, "which", return_value="pdftotext"),
                mock.patch.object(documents.subprocess, "run", return_value=failed),
            ):
                with self.assertRaisesRegex(ValueError, "pdf failed"):
                    documents.pdf_to_text(pdf_path.name, pdf_path)

            def extract_run(command, **_kwargs):
                Path(command[-1]).write_text("texto interno con suficientes caracteres para lectura normal. " * 2)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                mock.patch.object(documents.shutil, "which", return_value="pdftotext"),
                mock.patch.object(documents.subprocess, "run", side_effect=extract_run),
            ):
                text, detail, raw = documents.pdf_to_text(pdf_path.name, pdf_path)
            self.assertIn("pdftotext", detail)
            self.assertTrue(text and raw)

            def sparse_run(command, **_kwargs):
                Path(command[-1]).write_text("x")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                mock.patch.object(documents.shutil, "which", return_value="pdftotext"),
                mock.patch.object(documents.subprocess, "run", side_effect=sparse_run),
                mock.patch.object(documents, "ocr_pdf_to_text", return_value="[Pagina 1]\nOCR suficiente"),
            ):
                self.assertIn("OCR", documents.pdf_to_text(pdf_path.name, pdf_path)[1])

            self.assertEqual(documents.pdf_page_count(pdf_path), 0)
            info = subprocess.CompletedProcess([], 0, stdout="Pages:          12\n", stderr="")
            with (
                mock.patch.object(documents.shutil, "which", return_value="pdfinfo"),
                mock.patch.object(documents.subprocess, "run", return_value=info),
            ):
                self.assertEqual(documents.pdf_page_count(pdf_path), 12)

            with mock.patch.object(documents.shutil, "which", return_value=None):
                with self.assertRaisesRegex(ValueError, "ocr_tools_not_found"):
                    documents.ocr_pdf_to_text(pdf_path)
            with (
                mock.patch.object(documents.shutil, "which", side_effect=["pdftoppm", "tesseract"]),
                mock.patch.object(documents, "pdf_page_count", return_value=0),
            ):
                with self.assertRaisesRegex(ValueError, "pdf_page_count_not_found"):
                    documents.ocr_pdf_to_text(pdf_path)
            with (
                mock.patch.object(documents.shutil, "which", side_effect=["pdftoppm", "tesseract"]),
                mock.patch.object(documents, "pdf_page_count", return_value=2),
                mock.patch.object(documents, "OCR_WORKERS", 1),
                mock.patch.object(documents, "ocr_pdf_page_to_text", side_effect=[(1, "uno"), (2, "")]),
            ):
                self.assertIn("Pagina 1", documents.ocr_pdf_to_text(pdf_path))
            with (
                mock.patch.object(documents.shutil, "which", side_effect=["pdftoppm", "tesseract"]),
                mock.patch.object(documents, "pdf_page_count", return_value=3),
                mock.patch.object(documents, "OCR_WORKERS", 3),
                mock.patch.object(
                    documents, "ocr_pdf_page_to_text", side_effect=lambda _p, page, *_a: (page, str(page))
                ),
            ):
                self.assertIn("Pagina 3", documents.ocr_pdf_to_text(pdf_path))

            render_failed = subprocess.CompletedProcess([], 1, stdout="", stderr="render failed")
            with mock.patch.object(documents.subprocess, "run", return_value=render_failed):
                with self.assertRaisesRegex(ValueError, "render failed"):
                    documents.ocr_pdf_page_to_text(pdf_path, 1, "pdftoppm", "tesseract")
            render_ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with mock.patch.object(documents.subprocess, "run", return_value=render_ok):
                self.assertEqual(documents.ocr_pdf_page_to_text(pdf_path, 1, "pdftoppm", "tesseract"), (1, ""))

            with mock.patch.object(documents.subprocess, "run", return_value=render_failed):
                with self.assertRaises(ValueError):
                    documents.run_tesseract_plain("tesseract", pdf_path)
            with mock.patch.object(
                documents.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="texto", stderr=""),
            ):
                self.assertEqual(documents.run_tesseract_plain("tesseract", pdf_path), "texto")

            with mock.patch.object(documents.shutil, "which", return_value=None):
                with self.assertRaisesRegex(ValueError, "libreoffice_not_found"):
                    documents.office_to_text("sample.doc", b"data")

            def office_run(command, **_kwargs):
                out_dir = Path(command[command.index("--outdir") + 1])
                (out_dir / "sample.txt").write_text("Texto office", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                mock.patch.object(documents.shutil, "which", return_value="libreoffice"),
                mock.patch.object(documents.subprocess, "run", side_effect=office_run),
            ):
                self.assertEqual(documents.office_to_text("sample.doc", b"data"), "Texto office")
            with (
                mock.patch.object(documents.shutil, "which", return_value="libreoffice"),
                mock.patch.object(documents.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
            ):
                with self.assertRaisesRegex(ValueError, "libreoffice_output_not_found"):
                    documents.office_to_text("missing.doc", b"data")

    def test_pdf_cleanup_and_ocr_branch_matrix(self) -> None:
        for line in ("", "[Pagina 1]", "12", "A B C D", "Texto normal"):
            self.assertIsInstance(documents._is_mechanical_pdf_line(line), bool)
        for previous, current in (
            ("", "texto"),
            ("[Pagina 1]", "texto"),
            ("palabra-", "unida"),
            ("Final.", "Nueva"),
            ("continúa", "y sigue"),
        ):
            self.assertIsInstance(documents._should_join_pdf_lines(previous, current), bool)
        with mock.patch.object(documents, "_spanish_wordlist", return_value={"palabra"}):
            self.assertEqual(documents._join_fragments_if_known(("pala", "bra")), "palabra")
            self.assertIsNone(documents._join_fragments_if_known(("otra", "cosa")))
        self.assertEqual(documents.clean_pdf_text(""), "")
        self.assertEqual(documents.stopword_ratio("123"), 0.0)
        noisy = "ÍNDICE\n" + "\n".join(f"CAPÍTULO {index}" for index in range(1, 8))
        self.assertIsInstance(documents.looks_like_noisy_index_page(noisy), bool)
        self.assertIn("entregado", documents.postprocess_ocr_text("me fue entrega\nva por"))
        self.assertEqual(documents.normalize_heading_case(""), "")
        self.assertEqual(documents.heading_level("INTRODUCCIÓN"), "##")
        self.assertEqual(documents.heading_level("Una sección", previous_was_chapter=True), "##")

        bad_tsv = (
            "block_num\tpar_num\tline_num\ttop\tleft\tconf\ttext\n"
            "x\t1\t1\t1\t1\tbad\ttexto\n"
            "1\tx\t1\t1\t1\t90\ttexto\n"
            "1\t1\t1\t1\t1\t10\tbajo\n"
            "1\t1\t1\t1\t1\t90\t@@@\n"
        )
        self.assertEqual(documents.ocr_lines_from_tsv(bad_tsv), [])
        for line, conf in (("ab", 99), ("@@@@ texto largo", 99), ("texto suficientemente largo @@@@@", 99)):
            self.assertIsInstance(documents.keep_ocr_line(line, conf), bool)
        enough = [
            {"text": "Esta línea contiene texto suficiente para evaluar señal", "conf": 90, "block": 1, "par": i}
            for i in range(4)
        ]
        self.assertTrue(documents.enough_page_signal(enough))
        self.assertIn("texto suficiente", documents.format_ocr_lines(enough))

    def test_structured_image_ocr_and_signal_rejection_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "page.png"
            image = documents.Image.new("RGB", (1000, 1600), "white")
            image.save(source)

            ocr_parts = [
                "CAPÍTULO UNO",
                "Esta es una línea extensa con palabras normales para una lectura clara.\n"
                "La segunda línea también contiene suficiente información documental.",
                "Una tercera línea completa la página con una señal lingüística estable.\n"
                "El texto conserva palabras comunes y vocales propias del español.",
            ]
            with (
                mock.patch.object(documents, "run_tesseract_plain", side_effect=ocr_parts),
                mock.patch.object(documents, "enough_plain_page_signal", return_value=True),
            ):
                accepted = documents.structured_ocr_image(source, "tesseract")
            self.assertIn("CAPÍTULO UNO", accepted)

            with mock.patch.object(documents, "run_tesseract_plain", return_value=""):
                self.assertEqual(documents.structured_ocr_image(source, "tesseract"), "")

            plain_crop = root / "plain-crop.png"
            documents.save_ocr_crop(image, (0, 0, 20, 20), plain_crop)
            self.assertTrue(plain_crop.is_file())

        structured = documents.structured_plain_ocr_text(
            "texto descartado\nCAPÍTULO I\nCAPÍTULO I\n123 456\nTexto válido con suficientes palabras para conservarse",
            headings_only=True,
        )
        self.assertIn("Capítulo I", structured)
        self.assertNotIn("descartado", structured)
        self.assertEqual(documents.structured_plain_ocr_text("ab\n123 456"), "")
        self.assertFalse(documents.looks_like_numeric_artifact(""))
        self.assertFalse(documents.keep_ocr_line("@@@abc@@@", 90))
        self.assertFalse(documents.keep_ocr_line("palabra " + "@" * 30, 90))
        self.assertFalse(documents.keep_ocr_line("palabraunicasinsegmentacionlarga", 90))

        long_digits = "1234567890 " * 5
        self.assertFalse(documents.enough_plain_page_signal("\n".join([long_digits] * 3)))
        two_lines = "palabra común para una lectura documental estable " * 4
        self.assertFalse(documents.enough_plain_page_signal("\n".join([two_lines] * 2)))
        valid_lines = "\n".join(
            [
                "Texto documental suficientemente largo con palabras claras para lectura",
                "Otra línea contiene vocabulario español normal y varias palabras comunes",
                "La última línea completa el contenido requerido para analizar esta página",
            ]
        )
        with mock.patch.object(documents, "looks_like_noisy_index_page", return_value=True):
            self.assertFalse(documents.enough_plain_page_signal(valid_lines))
        with mock.patch.object(documents, "stopword_ratio", return_value=0.0):
            self.assertFalse(documents.enough_plain_page_signal(valid_lines))
        self.assertTrue(documents.enough_plain_page_signal("CAPÍTULO UNO\n" + valid_lines))
        without_stopwords = "\n".join(["brt crd frg plm str trn vrs znd " * 3] * 3)
        with mock.patch.object(documents, "stopword_ratio", return_value=0.2):
            self.assertFalse(documents.enough_plain_page_signal(without_stopwords))

        short_lines = [{"text": "x" * 50, "conf": 90, "block": 1, "par": index} for index in range(2)]
        self.assertFalse(documents.enough_page_signal(short_lines))
        two_long_lines = [{"text": "x" * 70, "conf": 90, "block": 1, "par": index} for index in range(2)]
        self.assertFalse(documents.enough_page_signal(two_long_lines))
        low_confidence = [
            {"text": "Texto suficientemente largo para evaluar señal", "conf": 1, "block": 1, "par": index}
            for index in range(3)
        ]
        self.assertFalse(documents.enough_page_signal(low_confidence))


class MarkdownAndPDFEditorialMatrixTests(unittest.TestCase):
    def test_markdown_sanitizer_and_segmentation_preserve_content(self) -> None:
        source = (
            "data:image/png;base64,AAAA\n![imagen](x.png)\n<!-- image -->\n"
            "# CAPÍTULO UNO\n\nEsteesuntexto pegado. Hola,mundo.\n"
            "CABECERA REPETIDA\nPágina 1\nCABECERA REPETIDA\n"
        )
        self.assertNotIn("base64", markdown.remove_image_placeholders(source).lower())
        normalized, stats = markdown.normalize_spanish_ocr_v4("l a casa 0 el camin0")
        self.assertIsInstance(stats, dict)
        self.assertTrue(normalized)
        self.assertTrue(markdown.is_protected_term_v4("Bonisagus"))
        self.assertFalse(markdown.is_protected_term_v4("palabradesconocida"))
        detected = markdown.detect_suspicious_glued_tokens("paraque desdeentonces textolargosinpausas")
        self.assertIn("suspicious_count", detected)
        segmented, changed, confidence = markdown.segment_glued_token_v4("casaazul", {"casa", "azul"})
        self.assertIsInstance(segmented, str)
        self.assertIsInstance(changed, bool)
        self.assertGreaterEqual(confidence, 0.0)
        repaired, repair_stats = markdown.repair_glued_words_v4("paraque desdeentonces")
        self.assertIn("para que", repaired.lower())
        self.assertIsInstance(repair_stats, dict)
        self.assertEqual(markdown.fix_punctuation_spacing("Hola,mundo."), "Hola, mundo.")
        header_lines = ["Ars Magica", *[f"Texto {index}" for index in range(10)]] * 3
        headers = markdown.remove_repeated_running_headers(header_lines)
        self.assertEqual(headers.count("Ars Magica"), 0)
        sanitized = markdown.sanitize_markdown(source)
        self.assertNotIn("base64", sanitized.lower())

    def test_markdown_dynamic_branch_and_conversion_matrix(self) -> None:
        self.assertEqual(markdown._preserve_initial_case_v4("", "casa azul"), "casa azul")
        self.assertEqual(markdown._preserve_initial_case_v4("CASA", "casa azul"), "CASA azul")
        self.assertEqual(markdown._preserve_initial_case_v4("Casa", "casa azul"), "Casa azul")
        self.assertEqual(markdown._canonical_word_v4("disenio"), "diseño")
        normalized, metrics = markdown.normalize_spanish_ocr_v4("magica Ars Magica ano de 2024 disenio")
        self.assertIn("mágica", normalized)
        self.assertIn("año", normalized)
        self.assertGreater(metrics["accent_fixes"], 0)
        self.assertIn("diseño", markdown.normalize_common_ocr_errors("disenio"))

        for token, expected in (
            ("", False),
            ("Bonisagus", False),
            ("responsabilidad", False),
            ("paraque", True),
            ("holaMundo", True),
            ("normal", False),
        ):
            self.assertEqual(markdown._is_suspicious_glued_token_v4(token), expected)
        self.assertEqual(markdown.segment_glued_token_v4(""), ("", False, 0.0))
        self.assertEqual(markdown.segment_glued_token_v4("Bonisagus"), ("Bonisagus", False, 0.0))
        self.assertTrue(markdown.segment_glued_token_v4("paraque")[1])
        self.assertFalse(markdown.segment_glued_token_v4("normal")[1])
        self.assertFalse(markdown.segment_glued_token_v4("holaMundo", {"nada"})[1])
        self.assertTrue(markdown.segment_glued_token_v4("casaAzul", {"casa", "azul"})[1])

        self.assertEqual(markdown._split_connector_span_v4("modelos"), ("modelos", False))
        self.assertEqual(markdown._split_connector_span_v4("paraque"), ("para que", True))
        self.assertTrue(markdown._split_connector_span_v4("casaparaqueleer")[1])
        self.assertFalse(markdown._split_connector_span_v4("xparaquey")[1])
        self.assertFalse(markdown._split_connector_span_v4("sinconector")[1])
        repaired, repaired_metrics = markdown.repair_glued_words_v4(
            "Bonisagus paraque desdeentonces ArtesMagicas palabra"
        )
        self.assertIn("para que", repaired)
        self.assertGreaterEqual(repaired_metrics["protected_terms_skipped"], 1)
        self.assertEqual(markdown.repair_glued_words("paraque"), "para que")

        markdown_source = """# Título
## Capítulo
### Sección
#### Subsección
- viñeta
* otra viñeta
1. numerada
| celda | valor |
| --- | --- |
Párrafo normal.
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "sample.md"
            target = root / "sample.docx"
            source_path.write_text(markdown_source, encoding="utf-8")
            markdown.convert_md_to_docx(source_path, target)
            self.assertTrue(zipfile.is_zipfile(target))
            with self.assertRaises(SystemExit):
                markdown.convert_md_to_docx(root / "missing.md", target)
            with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaises(SystemExit):
                    markdown.convert_md_to_docx(source_path, target)

    def test_pdf_editorial_classification_and_minimal_docx(self) -> None:
        page = (
            "REVISTA ACADÉMICA 2024\n12\nCAPÍTULO I\n"
            "Esta es la primera línea de un párrafo que\ncontinúa en minúscula.\n\n"
            "1. Sección breve\nTexto final."
        )
        blocks, warnings, metrics = pdf._page_paragraphs(page)
        self.assertTrue(blocks)
        self.assertIsInstance(warnings, list)
        self.assertIn("merged", metrics)
        for line in ("12", "x", "---", "Texto válido", "REVISTA 2024"):
            self.assertIsInstance(pdf._is_noise_line(line), bool)
            self.assertIsInstance(pdf._detect_heading(line), (str, type(None)))
        for previous, current in (
            ("frase que", "continúa"),
            ("frase completa.", "Nueva"),
            ("guion-", "unido"),
        ):
            self.assertIsInstance(pdf._should_merge_with_previous(previous, current), bool)
        self.assertEqual(pdf._join_paragraph_lines(["pala-", "bra final"]), "palabra final")
        for text in ("TÍTULO", "CAPÍTULO IV", "1. Sección", "Texto normal"):
            self.assertIn(pdf._classify_style(text), {"Title", "Heading1", "Heading2", "Normal"})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "minimal.docx"
            pdf._write_minimal_docx(target, blocks)
            self.assertTrue(zipfile.is_zipfile(target))
            with zipfile.ZipFile(target) as archive:
                self.assertIn("word/document.xml", archive.namelist())
            count, notes, build_metrics = pdf.build_docx_from_pdf_structure([page, "SEGUNDA PÁGINA\nTexto"], target)
            self.assertGreater(count, 0)
            self.assertGreaterEqual(notes, 0)
            self.assertIn("headings", build_metrics)
        for xml in (pdf._styles_xml(), pdf._content_types_xml(), pdf._rels_xml(), pdf._core_xml(), pdf._app_xml()):
            self.assertTrue(xml.startswith("<?xml"))

    def test_pdf_conversion_engine_and_subprocess_branch_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.pdf"
            source.write_bytes(b"%PDF")
            output = root / "out.docx"
            events: list[str] = []

            with mock.patch.object(pdf.shutil, "which", return_value=None):
                self.assertEqual(pdf._page_count(source), 0)
                with self.assertRaisesRegex(RuntimeError, "pdftotext_not_found"):
                    pdf._pdftotext_page(source, None)

            with (
                mock.patch.object(pdf, "_page_count", return_value=0),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=False),
                mock.patch.object(pdf, "_extract_pages_text", side_effect=RuntimeError("pdftotext_not_found")),
            ):
                unavailable = pdf.convert_pdf_to_docx(source, output)
            self.assertFalse(unavailable.ok)
            self.assertIn("pdftotext_not_found", unavailable.error)

            def callback(job: pdf.JobStatus) -> None:
                events.append(job.stage)

            extraction_job = pdf.JobStatus("extract-error")
            with (
                mock.patch.object(pdf, "_page_count", return_value=0),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=False),
                mock.patch.object(pdf, "_extract_pages_text", side_effect=OSError("missing")),
            ):
                extraction_error = pdf.convert_pdf_to_docx(source, output, callback, extraction_job)
            self.assertFalse(extraction_error.ok)
            self.assertEqual(extraction_job.state, "error")
            self.assertIn("missing", extraction_job.error)

            extraction_without_callback = pdf.JobStatus("extract-error-no-callback")
            with (
                mock.patch.object(pdf, "_page_count", return_value=0),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=False),
                mock.patch.object(pdf, "_extract_pages_text", side_effect=subprocess.SubprocessError("failed")),
            ):
                self.assertFalse(pdf.convert_pdf_to_docx(source, output, job=extraction_without_callback).ok)

            job = pdf.JobStatus("docling")
            docling_result = pdf.ConversionResult(True, output_path=str(output), engine="docling_gpu")
            with (
                mock.patch.object(pdf, "_page_count", return_value=2),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=True),
                mock.patch.object(pdf, "_convert_with_docling_gpu", return_value=docling_result),
            ):
                self.assertTrue(pdf.convert_pdf_to_docx(source, output, callback, job).ok)
            self.assertIn("docling_gpu", events)
            with (
                mock.patch.object(pdf, "_page_count", return_value=2),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=True),
                mock.patch.object(pdf, "_convert_with_docling_gpu", side_effect=RuntimeError("gpu")),
            ):
                self.assertIn("Fallo", pdf.convert_pdf_to_docx(source, output, callback, job).error)

            docling_without_callback = pdf.JobStatus("docling-no-callback")
            with (
                mock.patch.object(pdf, "_page_count", return_value=2),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=True),
                mock.patch.object(pdf, "_convert_with_docling_gpu", side_effect=RuntimeError("gpu")),
            ):
                self.assertIn("Fallo", pdf.convert_pdf_to_docx(source, output, job=docling_without_callback).error)

            with (
                mock.patch.object(pdf, "_page_count", return_value=1),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=False),
                mock.patch.object(pdf, "_extract_pages_text", return_value=["x"]),
            ):
                self.assertFalse(pdf.convert_pdf_to_docx(source, output, callback, pdf.JobStatus("scan")).ok)

            cancelled = pdf.JobStatus("cancelled", cancelled=True)
            with (
                mock.patch.object(pdf, "_page_count", return_value=1),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=False),
                mock.patch.object(pdf, "_extract_pages_text", return_value=["Texto suficientemente largo"]),
            ):
                self.assertIn("cancelada", pdf.convert_pdf_to_docx(source, output, job=cancelled).error)

            done = pdf.JobStatus("done")
            with (
                mock.patch.object(pdf, "_page_count", return_value=0),
                mock.patch.object(pdf, "is_docling_gpu_available", return_value=False),
                mock.patch.object(pdf, "_extract_pages_text", return_value=["Texto suficientemente largo"]),
                mock.patch.object(pdf, "build_docx_from_pdf_structure", return_value=(4, 1, {"headings": 2})),
            ):
                result = pdf.convert_pdf_to_docx(source, output, callback, done)
            self.assertTrue(result.ok)
            self.assertEqual(done.state, "done")
            self.assertEqual(result.pages, 1)

            failed_info = subprocess.CompletedProcess([], 1, stdout="", stderr="")
            with (
                mock.patch.object(pdf.shutil, "which", return_value="pdfinfo"),
                mock.patch.object(pdf.subprocess, "run", return_value=failed_info),
            ):
                self.assertEqual(pdf._page_count(source), 0)
            no_match = subprocess.CompletedProcess([], 0, stdout="no pages", stderr="")
            with (
                mock.patch.object(pdf.shutil, "which", return_value="pdfinfo"),
                mock.patch.object(pdf.subprocess, "run", return_value=no_match),
            ):
                self.assertEqual(pdf._page_count(source), 0)
            pages = subprocess.CompletedProcess([], 0, stdout="Pages: 3\n", stderr="")
            with (
                mock.patch.object(pdf.shutil, "which", return_value="pdfinfo"),
                mock.patch.object(pdf.subprocess, "run", return_value=pages),
            ):
                self.assertEqual(pdf._page_count(source), 3)

            with (
                mock.patch.object(pdf, "_page_count", return_value=0),
                mock.patch.object(pdf, "_pdftotext_page", return_value="uno\fdos"),
            ):
                self.assertEqual(pdf._extract_pages_text(source), ["uno", "dos"])
            with (
                mock.patch.object(pdf, "_page_count", return_value=3),
                mock.patch.object(pdf, "_pdftotext_page", side_effect=lambda _p, page: str(page)),
            ):
                self.assertEqual(pdf._extract_pages_text(source, limit=2), ["1", "2"])

            with mock.patch.object(
                pdf.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="List of languages\nspa\neng\n", stderr=""),
            ):
                self.assertEqual(pdf._tesseract_langs(), ["spa", "eng"])
            with mock.patch.object(pdf.subprocess, "run", side_effect=OSError("missing")):
                self.assertEqual(pdf._tesseract_langs(), ["eng"])

            with mock.patch.object(pdf.shutil, "which", return_value=None):
                pdf._preprocess_image(source)
            with (
                mock.patch.object(pdf.shutil, "which", return_value="convert"),
                mock.patch.object(pdf.subprocess, "run", side_effect=OSError("failed")),
            ):
                pdf._preprocess_image(source)

            failed_text = subprocess.CompletedProcess([], 1, stdout="", stderr="pdftotext failed")
            with (
                mock.patch.object(pdf.shutil, "which", return_value="pdftotext"),
                mock.patch.object(pdf.subprocess, "run", return_value=failed_text),
            ):
                with self.assertRaisesRegex(RuntimeError, "pdftotext failed"):
                    pdf._pdftotext_page(source, 1)
            ok_text = subprocess.CompletedProcess([], 0, stdout="texto", stderr="")
            with (
                mock.patch.object(pdf.shutil, "which", return_value="pdftotext"),
                mock.patch.object(pdf.subprocess, "run", return_value=ok_text) as run,
            ):
                self.assertEqual(pdf._pdftotext_page(source, None), "texto")
                self.assertNotIn("-f", run.call_args.args[0])

            with mock.patch.object(pdf, "_page_count", return_value=301):
                with self.assertRaisesRegex(ValueError, "demasiado largo"):
                    pdf._ocr_pdf_pages(source, max_pages=300)
            cancelled_ocr = pdf.JobStatus("ocr", cancelled=True)
            with (
                mock.patch.object(pdf, "_page_count", return_value=1),
                mock.patch.object(pdf, "_tesseract_langs", return_value=["eng"]),
            ):
                with self.assertRaisesRegex(RuntimeError, "cancelada"):
                    pdf._ocr_pdf_pages(source, job=cancelled_ocr)

    def test_docling_availability_environment_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"FUSION_READER_DOCLING_GPU_ENV": str(root)}):
                self.assertFalse(pdf.is_docling_gpu_available())
                python = root / "bin" / "python3"
                python.parent.mkdir()
                python.write_text("", encoding="utf-8")
                available = subprocess.CompletedProcess([], 0, stdout="True\n", stderr="")
                with mock.patch.object(pdf.subprocess, "run", return_value=available):
                    self.assertTrue(pdf.is_docling_gpu_available())
                with mock.patch.object(pdf.subprocess, "run", side_effect=OSError("failed")):
                    self.assertFalse(pdf.is_docling_gpu_available())


if __name__ == "__main__":
    unittest.main()
