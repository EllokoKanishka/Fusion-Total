import unittest
import os
import tempfile
import zipfile
import time
from pathlib import Path
from unittest import mock
from fusion_reader_v2.documents import import_document_bytes, structured_plain_ocr_text, repair_ocr_spacing
from fusion_reader_v2.pdf_to_docx import (
    _clean_ocr_line,
    _get_docling_gpu_env,
    _is_noise_line,
    _detect_heading,
    _should_merge_with_previous,
    ParagraphBlock,
    JobStatus,
    convert_pdf_to_docx,
    safe_output_name,
    find_downloads_dir,
)
from tests.helpers import (
    test_app,
    make_simple_pdf_bytes,
)

class PDFToWordTests(unittest.TestCase):
    def test_pdf_to_word_ui_exists_and_uses_compact_tool_naming(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("PDF → Word", server)
        self.assertIn("pdfToWordTool", server)


    def test_import_plain_text_document(self):
        doc = import_document_bytes("cuento.txt", "Uno.\n\nDos.".encode("utf-8"))
        self.assertEqual(doc.doc_id, "cuento")
        self.assertEqual(doc.source_type, "text")
        self.assertIn("Dos.", doc.text)

    def test_import_docx_document(self):
        root = Path(tempfile.mkdtemp())
        path = root / "cuento.docx"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Primer parrafo.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Segundo parrafo.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("word/document.xml", xml)
        doc = import_document_bytes("cuento.docx", path.read_bytes())
        self.assertEqual(doc.source_type, "docx")
        self.assertIn("Primer parrafo.", doc.text)

    def test_import_odt_document(self):
        root = Path(tempfile.mkdtemp())
        path = root / "cuento.odt"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text><text:p>Linea uno.</text:p></office:text></office:body>
</office:document-content>"""
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("content.xml", xml)
        doc = import_document_bytes("cuento.odt", path.read_bytes())
        self.assertEqual(doc.source_type, "odt")
        self.assertIn("Linea uno.", doc.text)

    def test_ocr_text_keeps_headings_and_paragraphs(self):
        raw = "Capítulo 1\nIntroducción\n\nEste es un párrafo de prueba."
        text = structured_plain_ocr_text(raw)
        self.assertIn("# Capítulo 1", text)
        self.assertIn("## Introducción", text)

    def test_ocr_processing_utils(self):
        # 1. Cleaning
        self.assertEqual(_clean_ocr_line("...  Texto sucio!!!"), "Texto sucio!!!")
        self.assertEqual(_clean_ocr_line("Palabra — con raya"), "Palabra - con raya")
        
        # 2. Noise
        self.assertTrue(_is_noise_line("A E A E A E"))
        self.assertTrue(_is_noise_line("... --- ..."))
        self.assertFalse(_is_noise_line("Este es un párrafo válido."))
        
        # 3. Headings
        self.assertEqual(_detect_heading("CAPÍTULO 1: EL COMIENZO"), "Heading1")
        self.assertEqual(_detect_heading("Introducción"), "Heading2")
        self.assertIsNone(_detect_heading("Esta es una línea normal que termina en punto."))
        
        # 4. Merging
        self.assertTrue(_should_merge_with_previous("Esta línea no termina en punto", "esta continúa"))
        self.assertFalse(_should_merge_with_previous("Esta sí termina en punto.", "Esta es nueva"))
        self.assertTrue(_should_merge_with_previous("Palabra cortada-", "continuación"))

    def test_repair_ocr_spacing_fixes_common_scan_merges(self):
        text = repair_ocr_spacing("Elabad llegó en elaño nuevo.")
        self.assertIn("El abad", text)
        self.assertIn("el año", text)

    def test_safe_output_name_strips_weird_input_and_keeps_docx_suffix(self):
        out = safe_output_name("../hola rara?.pdf")
        self.assertEqual(Path(out).name, out)
        self.assertTrue(out.endswith(".docx"))

    def test_find_downloads_dir_prefers_descargas_then_downloads_then_safe_fallback(self):
        with mock.patch("fusion_reader_v2.pdf_to_docx.Path.home", return_value=Path("/tmp/fake-home")):
            with mock.patch("fusion_reader_v2.pdf_to_docx.Path.exists", return_value=True):
                self.assertEqual(find_downloads_dir(), Path("/tmp/fake-home/Descargas"))

    def test_docling_gpu_env_prefers_env_and_otherwise_uses_repo_relative_default(self):
        previous = os.environ.get("FUSION_READER_DOCLING_GPU_ENV")
        try:
            os.environ["FUSION_READER_DOCLING_GPU_ENV"] = "/tmp/docling-env"
            self.assertEqual(_get_docling_gpu_env(), Path("/tmp/docling-env"))
            os.environ.pop("FUSION_READER_DOCLING_GPU_ENV", None)
            expected = Path("fusion_reader_v2/pdf_to_docx.py").resolve().parents[1] / "runtime" / "fusion_reader_v2" / "pdf_engine_benchmark" / "venvs" / "docling_gpu_venv"
            self.assertEqual(_get_docling_gpu_env(), expected)
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_DOCLING_GPU_ENV", None)
            else:
                os.environ["FUSION_READER_DOCLING_GPU_ENV"] = previous

    def test_pdf_to_docx_conversion_creates_real_docx_with_text(self):
        root = Path(tempfile.mkdtemp())
        pdf = root / "p.pdf"
        docx = root / "p.docx"
        pdf.write_bytes(make_simple_pdf_bytes(["Capitulo 1", "Realidad"]))
        result = convert_pdf_to_docx(pdf, docx)
        self.assertTrue(result.ok)
        self.assertTrue(docx.exists())

    def test_pdf_to_word_ui_is_compact_and_correct(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('id="pdfToWordTool"', server)
        self.assertNotIn("Soltá un PDF", server)

    def test_server_pdf_to_word_limit_is_500mb(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn('500 * 1024 * 1024', server)

    @mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=False)
    def test_pdf_to_word_ocr_fallback_logic(self, mock_gpu):
        with mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=[" "]), \
             mock.patch("fusion_reader_v2.pdf_to_docx._page_count", return_value=1):
            res = convert_pdf_to_docx("d.pdf", "o.docx")
            self.assertFalse(res.ok)
            self.assertIn("Motor Docling GPU no disponible", res.error)

    @mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=False)
    def test_pdf_to_word_job_progress(self, mock_gpu):
        job = JobStatus(job_id="t")
        progress = []
        with mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=["Texto largo digital."]), \
             mock.patch("fusion_reader_v2.pdf_to_docx._write_minimal_docx"), \
             mock.patch("fusion_reader_v2.pdf_to_docx._page_count", return_value=1):
            convert_pdf_to_docx("d.pdf", "o.docx", status_callback=lambda j: progress.append(j.stage), job=job)
            self.assertIn("done", [job.state])
            self.assertIn("preflight", progress)

    @mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=True)
    def test_pdf_to_word_docling_gpu_selection(self, mock_gpu):
        with mock.patch("fusion_reader_v2.pdf_to_docx._convert_with_docling_gpu") as mock_conv:
            mock_conv.return_value = mock.MagicMock(ok=True, engine="docling_gpu")
            res = convert_pdf_to_docx("d.pdf", "o.docx")
            self.assertEqual(res.engine, "docling_gpu")

    @mock.patch("fusion_reader_v2.pdf_to_docx.is_docling_gpu_available", return_value=False)
    def test_pdf_to_word_no_docling_gpu_fallback_for_scans(self, mock_gpu):
        with mock.patch("fusion_reader_v2.pdf_to_docx._extract_pages_text", return_value=[""]):
            res = convert_pdf_to_docx("d.pdf", "o.docx")
            self.assertFalse(res.ok)

    def test_md_to_docx_sanitization_v2(self):
        from fusion_reader_v2.md_to_docx import sanitize_markdown
        md = "# T\n![I](data:image/png;base64,A)\n<!-- image -->\n福"
        san = sanitize_markdown(md)
        self.assertNotIn("data:image", san)
        self.assertNotIn("福", san)

    def test_md_to_docx_glued_words(self):
        from fusion_reader_v2.md_to_docx import sanitize_markdown
        md = "Diariode Antoninus. todoslos Magos."
        san = sanitize_markdown(md)
        self.assertIn("Diario de", san)
        self.assertIn("todos los", san)

    def test_md_to_docx_glued_words_v4_real_ars_magica_examples(self):
        from fusion_reader_v2.md_to_docx import sanitize_markdown
        self.assertIn("Diario de", sanitize_markdown("Diariode Antoninus"))

from tests.helpers import attach_legacy_tests

attach_legacy_tests(PDFToWordTests, (
    "test_clean_heading_preserves_chapter_number",
    "test_pdf_to_word_docling_uses_placeholder",
    "test_pdf_to_word_ocr_cleanup_logic",
))
