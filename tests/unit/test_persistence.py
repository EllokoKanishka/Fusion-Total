from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2.services.persistence import AtomicJSONStore
from tests.helpers import managed_test_app


class AtomicJSONStoreTests(unittest.TestCase):
    def test_write_is_versioned_and_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = AtomicJSONStore(path)
            store.write({"value": 7})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": 7, "schema_version": 1})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_and_truncated_json_is_preserved_then_recovers(self) -> None:
        for raw in ('{"value":', "not-json"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "state.json"
                path.write_text(raw, encoding="utf-8")
                store = AtomicJSONStore(path)
                self.assertEqual(store.read({"clean": True}), {"clean": True})
                self.assertFalse(path.exists())
                self.assertEqual(len(list(path.parent.glob("state.json.corrupt.*"))), 1)
                self.assertEqual(store.warnings[-1].code, "state_invalid_json")

    def test_legacy_state_is_backed_up_before_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"value": 3}\n', encoding="utf-8")
            store = AtomicJSONStore(path, schema_version=2, migrations={1: lambda value: {**value, "migrated": True}})
            loaded = store.read()
            self.assertEqual(loaded["schema_version"], 2)
            self.assertTrue(loaded["migrated"])
            self.assertEqual(len(list(path.parent.glob("state.json.backup.*"))), 1)

    def test_future_and_oversized_state_are_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            future = root / "future.json"
            future.write_text('{"schema_version": 99, "value": "future"}', encoding="utf-8")
            self.assertEqual(AtomicJSONStore(future).read({"safe": True}), {"safe": True})
            self.assertEqual(len(list(root.glob("future.json.corrupt.*"))), 1)

            large = root / "large.json"
            large.write_text('{"value": "0123456789"}', encoding="utf-8")
            self.assertEqual(AtomicJSONStore(large, max_bytes=8).read({"safe": True}), {"safe": True})
            self.assertEqual(len(list(root.glob("large.json.corrupt.*"))), 1)

    def test_concurrent_writes_always_leave_complete_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = AtomicJSONStore(path)
            barrier = threading.Barrier(8)

            def writer(index: int) -> None:
                barrier.wait()
                for iteration in range(20):
                    store.write({"writer": index, "iteration": iteration})

            threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema_version"], 1)
            self.assertIn(loaded["writer"], range(8))

    def test_replace_failure_preserves_previous_state_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = AtomicJSONStore(path)
            store.write({"value": "old"})
            with mock.patch("fusion_reader_v2.services.persistence.os.replace", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    store.write({"value": "new"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "old")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_permission_denied_while_reading_degrades_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"schema_version": 1}', encoding="utf-8")
            store = AtomicJSONStore(path)
            with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                self.assertEqual(store.read({"safe": True}), {"safe": True})
            self.assertEqual(store.warnings[-1].code, "state_invalid_json")

    def test_reader_startup_recovers_corrupt_session_without_touching_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session_state.json"
            session.write_text('{"doc_id":', encoding="utf-8")
            unrelated = root / "personal.txt"
            unrelated.write_text("unchanged", encoding="utf-8")
            with managed_test_app(root=root) as app:
                self.assertEqual(app.status()["doc_id"], "")
                self.assertTrue(app._session_store.warnings)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(len(list(root.glob("session_state.json.corrupt.*"))), 1)


if __name__ == "__main__":
    unittest.main()
