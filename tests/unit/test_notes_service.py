from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from fusion_reader_v2.notes import ReaderNotesStore
from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.notes import NotesService


class NotesServiceTests(unittest.TestCase):
    def test_document_crud_and_laboratory_history_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = ReaderSession()
            session.load(Document.from_text("doc", "Documento", "Primer bloque con contenido suficiente."))
            dialogue = [{"role": "assistant", "content": "Una idea reciente"}]
            service = NotesService(
                session=session,
                notes=ReaderNotesStore(Path(tmp) / "notes"),
                dialogue_history=dialogue,
                dialogue_lock=threading.Lock(),
                chat_history=[],
                chat_lock=threading.Lock(),
                looks_like_note_request=lambda text: text.lower().startswith("nota"),
            )
            created = service.create("Recordar esta idea")
            self.assertTrue(created["ok"])
            self.assertEqual(service.summary()["count"], 1)
            note_id = created["note"]["note_id"]
            self.assertTrue(service.update(note_id, "Idea corregida")["ok"])
            self.assertTrue(service.rename(note_id, "Clave")["ok"])
            self.assertTrue(service.delete(note_id)["ok"])
            laboratory = service.create_laboratory("eso")
            self.assertTrue(laboratory["ok"])
            self.assertEqual(laboratory["note"]["text"], "Una idea reciente")

    def test_service_does_not_need_a_facade_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = NotesService(
                session=ReaderSession(),
                notes=ReaderNotesStore(Path(tmp) / "notes"),
                dialogue_history=[],
                dialogue_lock=threading.Lock(),
                chat_history=[],
                chat_lock=threading.Lock(),
                looks_like_note_request=lambda _text: False,
            )
            self.assertEqual(service.summary(), {"ok": True, "count": 0, "current_count": 0})
            self.assertFalse(hasattr(service, "owner"))


if __name__ == "__main__":
    unittest.main()
