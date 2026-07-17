from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import documents
from fusion_reader_v2.documents import ArchiveLimits, docx_to_text, import_document_path, imported, odt_to_text


def archive(entries: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as target:
        for name, data in entries.items():
            target.writestr(name, data)
    return output.getvalue()


class DocumentArchiveSafetyTests(unittest.TestCase):
    def test_valid_docx_and_odt(self) -> None:
        docx = archive(
            {
                "word/document.xml": (
                    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    b"<w:p><w:r><w:t>Hola</w:t></w:r></w:p></w:document>"
                )
            }
        )
        odt = archive(
            {
                "content.xml": (
                    b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                    b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><text:p>Hola</text:p>'
                    b"</office:document-content>"
                )
            }
        )
        self.assertEqual(docx_to_text(docx), "Hola")
        self.assertEqual(odt_to_text(odt), "Hola")

    def test_corrupt_missing_suspicious_and_many_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "document_archive_invalid"):
            docx_to_text(b"not zip")
        with self.assertRaisesRegex(ValueError, "document_archive_invalid"):
            docx_to_text(archive({"other.xml": b"x"}))
        with self.assertRaisesRegex(ValueError, "document_archive_invalid"):
            docx_to_text(archive({"../word/document.xml": b"x"}))
        with self.assertRaisesRegex(ValueError, "document_archive_too_large"):
            docx_to_text(archive({"word/document.xml": b"<x/>", "a": b"x"}), limits=ArchiveLimits(max_entries=1))

    def test_ratio_xml_total_and_text_limits(self) -> None:
        bomb = archive({"word/document.xml": b"x" * 100_000})
        with self.assertRaisesRegex(ValueError, "document_archive_ratio_exceeded"):
            docx_to_text(bomb, limits=ArchiveLimits(max_compression_ratio=2))
        xml = archive({"word/document.xml": b"<x>abcdef</x>"}, compression=zipfile.ZIP_STORED)
        with self.assertRaisesRegex(ValueError, "document_xml_too_large"):
            docx_to_text(xml, limits=ArchiveLimits(max_xml_bytes=4))
        with self.assertRaisesRegex(ValueError, "document_archive_too_large"):
            docx_to_text(xml, limits=ArchiveLimits(max_total_bytes=4))
        text_xml = archive(
            {
                "word/document.xml": (
                    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    b"<w:p><w:r><w:t>abcdef</w:t></w:r></w:p></w:document>"
                )
            },
            compression=zipfile.ZIP_STORED,
        )
        with self.assertRaisesRegex(ValueError, "document_text_too_large"):
            docx_to_text(text_xml, limits=ArchiveLimits(max_text_chars=3))

    def test_office_import_and_empty_extraction_are_deterministic_without_libreoffice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "upload"
            source.write_bytes(b"synthetic office input")
            with mock.patch.object(documents, "office_to_text", return_value="Texto de oficina") as convert:
                result = import_document_path("sample.doc", source)
            self.assertEqual(result.source_type, "office")
            convert.assert_called_once_with("sample.doc", b"synthetic office input")
        with self.assertRaisesRegex(ValueError, "empty_extracted_text:text"):
            imported("empty.txt", "  ", "text", "test")


if __name__ == "__main__":
    unittest.main()
