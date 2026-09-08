from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2.dictation_workspace import DictationProjectStore, render_dictation_pdf


class DictationProjectStoreTests(unittest.TestCase):
    def test_projects_persist_with_voice_assistant_activity_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dictation_projects.json"
            store = DictationProjectStore(path)
            saved = store.save(
                {
                    "title": "Clase sobre el tiempo",
                    "text": "San Agustín piensa el tiempo.",
                    "voice": "M06__Lakshmi.wav",
                    "assistant": "local14b",
                    "commands_enabled": True,
                    "pdf_page_numbers": False,
                    "selection_start": 4,
                    "selection_end": 12,
                    "activity": ["Oí: Lucy, corregí el texto", "Corrección aplicada."],
                }
            )
            project_id = saved["project"]["project_id"]

            reopened = DictationProjectStore(path)
            loaded = reopened.load(project_id)
            self.assertEqual(loaded["title"], "Clase sobre el tiempo")
            self.assertEqual(loaded["voice"], "M06__Lakshmi.wav")
            self.assertEqual(loaded["assistant"], "local14b")
            self.assertFalse(loaded["pdf_page_numbers"])
            self.assertEqual((loaded["selection_start"], loaded["selection_end"]), (4, 12))
            self.assertEqual(len(loaded["activity"]), 2)
            self.assertEqual(reopened.list()[0]["project_id"], project_id)

    def test_save_updates_one_project_instead_of_creating_versions(self) -> None:
        store = DictationProjectStore(None)
        first = store.save({"title": "Uno", "text": "Texto inicial."})["project"]
        second = store.save({**first, "text": "Texto actualizado."})["project"]
        self.assertEqual(first["project_id"], second["project_id"])
        self.assertEqual(len(store.list()), 1)
        self.assertEqual(store.load(first["project_id"])["text"], "Texto actualizado.")

    def test_untitled_project_gets_a_readable_history_label_without_mutating_title(self) -> None:
        store = DictationProjectStore(None)
        saved = store.save(
            {
                "title": "Dictado sin título",
                "text": "La nostalgia ya no funciona como antes porque el presente cambió.",
            }
        )
        summary = saved["summary"]
        self.assertNotEqual(summary["title"], "Dictado sin título")
        self.assertEqual(saved["project"]["title"], "Dictado sin título")
        self.assertIn("nostalgia", summary["title"].lower())

    def test_delete_removes_only_the_requested_project(self) -> None:
        store = DictationProjectStore(None)
        first = store.save({"title": "Uno", "text": "A"})["project"]["project_id"]
        second = store.save({"title": "Dos", "text": "B"})["project"]["project_id"]
        store.delete(first)
        self.assertEqual([item["project_id"] for item in store.list()], [second])
        with self.assertRaises(KeyError):
            store.load(first)


class DictationPdfTests(unittest.TestCase):
    def test_academic_pdf_is_real_pdf_and_keeps_title_and_text(self) -> None:
        raw = render_dictation_pdf(
            "Ensayo sobre el presente",
            "Primer párrafo con ñ, tildes y comillas “curvas”.\n\nSegundo párrafo.",
            page_numbers=True,
        )
        self.assertTrue(raw.startswith(b"%PDF-"))
        self.assertGreater(len(raw), 1500)

        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            return
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "dictado.pdf"
            pdf.write_bytes(raw)
            result = subprocess.run(
                [pdftotext, "-layout", str(pdf), "-"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertIn("Ensayo sobre el presente", result.stdout)
            self.assertIn("Primer párrafo", result.stdout)
            self.assertIn("Segundo párrafo", result.stdout)

    def test_page_numbers_are_optional(self) -> None:
        text = " ".join(["Una frase para llenar varias líneas."] * 300)
        with_numbers = render_dictation_pdf("Con páginas", text, page_numbers=True)
        without_numbers = render_dictation_pdf("Con páginas", text, page_numbers=False)
        self.assertTrue(with_numbers.startswith(b"%PDF-"))
        self.assertTrue(without_numbers.startswith(b"%PDF-"))
        self.assertNotEqual(with_numbers, without_numbers)

    def test_empty_pdf_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty_dictation"):
            render_dictation_pdf("Vacío", "   ")


if __name__ == "__main__":
    unittest.main()
