from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fusion_reader_v2.notes import ReaderNote, ReaderNotesStore, safe_doc_id
from fusion_reader_v2.reader import ReaderSession
from fusion_reader_v2.services.notes import NotesService
from fusion_reader_v2.services.persistence import AtomicJSONStore
from tests.helpers import managed_test_app


class NoteIdentityTests(unittest.TestCase):
    def test_colliding_legacy_names_remain_separate_after_crud_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ReaderNotesStore(root)
            first = store.add("libro/a", "Primero", 0, "Nota de A")
            second = store.add("libro_a", "Segundo", 0, "Nota de B")
            self.assertEqual([x["text"] for x in store.list("libro/a")], ["Nota de A"])
            self.assertEqual([x["text"] for x in store.list("libro_a")], ["Nota de B"])
            store.update("libro/a", first["note_id"], "A corregida")
            with self.assertRaises(KeyError):
                store.delete("libro_a", first["note_id"])
            reopened = ReaderNotesStore(root)
            self.assertEqual([x["text"] for x in reopened.list("libro/a")], ["A corregida"])
            self.assertEqual([x["note_id"] for x in reopened.list("libro_a")], [second["note_id"]])

    def test_mixed_legacy_file_is_filtered_by_declared_owner_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / f"{safe_doc_id('libro/a')}.json"
            a = ReaderNote.create("libro/a", "A", 0, "Antigua A").to_dict()
            b = ReaderNote.create("libro_a", "B", 0, "Antigua B").to_dict()
            raw = json.dumps({"schema_version": 1, "doc_id": "libro_a", "notes": [a, b]}).encode()
            legacy.write_bytes(raw)
            store = ReaderNotesStore(root)
            self.assertEqual([x["text"] for x in store.list("libro/a")], ["Antigua A"])
            self.assertEqual([x["text"] for x in store.list("libro_a")], ["Antigua B"])
            store.update("libro/a", a["note_id"], "A editada")
            store.delete("libro_a", b["note_id"])
            self.assertEqual(legacy.read_bytes(), raw)
            reopened = ReaderNotesStore(root)
            self.assertEqual([x["text"] for x in reopened.list("libro/a")], ["A editada"])
            self.assertEqual(reopened.list("libro_a"), [])

    def test_legacy_list_without_owner_is_not_assigned_or_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "book.json"
            raw = b'[{"note_id":"unknown","text":"Sin identidad"}]'
            legacy.write_bytes(raw)
            store = ReaderNotesStore(root)
            self.assertEqual(store.list("book"), [])
            self.assertEqual(legacy.read_bytes(), raw)
            self.assertTrue(store.warnings)
            store.add("book", "Book", 0, "Nueva nota identificada")
            reopened = ReaderNotesStore(root)
            self.assertEqual([x["text"] for x in reopened.list("book")], ["Nueva nota identificada"])
            self.assertTrue(reopened.warnings)
            self.assertEqual(legacy.read_bytes(), raw)

    def test_legacy_list_with_declared_note_identity_stays_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = ReaderNote.create("book", "Book", 0, "Con identidad").to_dict()
            legacy = root / "book.json"
            raw = json.dumps([note]).encode()
            legacy.write_bytes(raw)
            store = ReaderNotesStore(root)
            self.assertEqual(store.list("book")[0]["note_id"], note["note_id"])
            self.assertEqual(legacy.read_bytes(), raw)

    def test_wrong_canonical_owner_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReaderNotesStore(tmp)
            target = store._path("B")
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(
                {
                    "schema_version": 1,
                    "doc_id": "A",
                    "notes": [
                        ReaderNote.create("A", "A", 0, "No es de B").to_dict(),
                    ],
                }
            ).encode()
            target.write_bytes(raw)
            self.assertEqual(store.list("B"), [])
            with self.assertRaisesRegex(ValueError, "notes_state_requires_review"):
                store.add("B", "B", 0, "Nueva B")
            self.assertEqual(target.read_bytes(), raw)

    def test_malformed_canonical_note_is_preserved_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReaderNotesStore(tmp)
            target = store._path("book")
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = b'{"schema_version":1,"doc_id":"book","notes":[{"doc_id":"book","chunk_index":{}}]}'
            target.write_bytes(raw)
            self.assertEqual(store.list("book"), [])
            with self.assertRaisesRegex(ValueError, "notes_state_requires_review"):
                store.add("book", "Book", 0, "No sobrescribir")
            self.assertEqual(target.read_bytes(), raw)


class RecoveryTests(unittest.TestCase):
    def test_invalid_session_fields_are_preserved_and_startup_degrades(self):
        for field, value in (
            ("reference_documents", 42),
            ("reference_documents", {"not": "a list"}),
            ("reference_documents", [42]),
            ("reference_documents", [{"doc_id": "ref", "text": {}}]),
            ("doc_id", ["bad"]),
            ("text", {"bad": "text"}),
        ):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root / "session_state.json"
                raw = json.dumps(
                    {"schema_version": 1, "doc_id": "book", "title": "Book", "text": "Texto", field: value}
                ).encode()
                state.write_bytes(raw)
                with managed_test_app(root=root) as app:
                    self.assertEqual(app.status()["doc_id"], "")
                    preserved = list(root.glob("session_state.json.corrupt.*"))
                    self.assertEqual(len(preserved), 1)
                    self.assertEqual(preserved[0].read_bytes(), raw)
                    self.assertTrue(app._session_store.warnings)

    def test_backup_failure_keeps_only_copy_and_protects_later_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            raw = b'{"schema_version":1,"value":"original"}'
            path.write_bytes(raw)
            store = AtomicJSONStore(path, schema_version=2)
            with patch("fusion_reader_v2.services.persistence.shutil.copy2", side_effect=PermissionError("denied")):
                self.assertEqual(store.read()["value"], "original")
                self.assertEqual(path.read_bytes(), raw)
                with self.assertRaises(OSError):
                    store.write({"value": "replacement"})
                self.assertEqual(path.read_bytes(), raw)
            store.write({"value": "replacement"})
            backups = list(path.parent.glob("state.json.backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), raw)
            self.assertEqual(json.loads(path.read_text())["value"], "replacement")

    def test_repeated_warnings_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJSONStore(Path(tmp) / "state.json")
            with patch.object(Path, "stat", side_effect=PermissionError("denied")):
                for _ in range(200):
                    store.read()
            self.assertLessEqual(len(store.warnings), 64)


class HistoryRecencyTests(unittest.TestCase):
    def make_service(self, root, dialogue, chat):
        return NotesService(
            session=ReaderSession(),
            notes=ReaderNotesStore(root),
            dialogue_history=dialogue,
            dialogue_lock=threading.Lock(),
            chat_history=chat,
            chat_lock=threading.Lock(),
            looks_like_note_request=lambda text: text.startswith("nota"),
        )

    def test_latest_exchange_drives_both_note_and_quote(self):
        for chat_newer in (True, False):
            with self.subTest(chat_newer=chat_newer), tempfile.TemporaryDirectory() as tmp:
                service = self.make_service(
                    tmp,
                    [{"role": "assistant", "content": "Hablado", "ts": 1 if chat_newer else 3}],
                    [{"role": "assistant", "content": "Escrito", "ts": 2}],
                )
                expected = "Escrito" if chat_newer else "Hablado"
                note = service.create_laboratory("eso")["note"]
                self.assertEqual(note["text"], expected)
                self.assertEqual(note["quote"], f"Laboratorio: {expected}")

    def test_untimed_histories_have_explicit_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(
                tmp,
                [{"role": "assistant", "content": "Hablado"}],
                [{"role": "assistant", "content": "Escrito"}],
            )
            self.assertEqual(service.recent_laboratory_target(), "Hablado")

    def test_production_chat_and_dialogue_histories_record_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp, managed_test_app(root=Path(tmp)) as app:
            app.chat("Primera pregunta filosófica")
            app.dialogue_turn_text("Otra pregunta filosófica")
            self.assertTrue(app._chat_history)
            self.assertTrue(app._dialogue_history)
            self.assertTrue(
                all(isinstance(x.get("ts"), (float, int)) for x in app._chat_history + app._dialogue_history)
            )
