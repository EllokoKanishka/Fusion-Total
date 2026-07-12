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
    def test_constructor_and_write_reject_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            AtomicJSONStore("state.json", schema_version=0)
        with self.assertRaises(ValueError):
            AtomicJSONStore("state.json", max_bytes=0)
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJSONStore(Path(tmp) / "state.json", max_bytes=32)
            with self.assertRaises(TypeError):
                store.write([])  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                store.write({"value": "x" * 100})

    def test_missing_state_returns_isolated_callable_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJSONStore(Path(tmp) / "missing.json")
            loaded = store.read(lambda: {"items": []})
            loaded["items"].append("changed")
            self.assertEqual(store.read(lambda: {"items": []}), {"items": []})

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

    def test_stat_shape_version_and_legacy_transform_failures_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stat_path = root / "stat.json"
            store = AtomicJSONStore(stat_path)
            with mock.patch.object(Path, "stat", side_effect=PermissionError("stat denied")):
                self.assertEqual(store.read({"safe": True}), {"safe": True})
            self.assertEqual(store.warnings[-1].code, "state_stat_failed")

            cases = (
                ("array.json", "[]", None, "state_invalid_shape"),
                ("version.json", '{"schema_version": "bad"}', None, "state_invalid_version"),
                ("legacy.json", "[]", lambda _value: (_ for _ in ()).throw(ValueError("bad")), "state_invalid_shape"),
            )
            for filename, raw, transform, warning in cases:
                path = root / filename
                path.write_text(raw, encoding="utf-8")
                candidate = AtomicJSONStore(path)
                self.assertEqual(candidate.read({"safe": True}, legacy_transform=transform), {"safe": True})
                self.assertEqual(candidate.warnings[-1].code, warning)

    def test_migration_failures_backup_warning_and_write_warning_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, migration in (
                ("wrong-type", lambda _value: []),
                ("missing-key", lambda _value: {}["missing"]),
            ):
                path = root / f"{label}.json"
                path.write_text('{"schema_version": 1}', encoding="utf-8")
                store = AtomicJSONStore(path, schema_version=2, migrations={1: migration})
                self.assertEqual(store.read({"safe": True}), {"safe": True})
                self.assertEqual(store.warnings[-1].code, "state_migration_failed")

            backup = root / "backup.json"
            backup.write_text('{"schema_version": 1}', encoding="utf-8")
            backup_store = AtomicJSONStore(backup, schema_version=2)
            with mock.patch("fusion_reader_v2.services.persistence.shutil.copy2", side_effect=PermissionError("no")):
                migrated = backup_store.read()
            self.assertEqual(migrated["schema_version"], 2)
            self.assertIn("state_backup_failed", {warning.code for warning in backup_store.warnings})

            write_path = root / "write.json"
            write_path.write_text('{"schema_version": 1}', encoding="utf-8")
            write_store = AtomicJSONStore(write_path, schema_version=2)
            with mock.patch.object(write_store, "_write_locked", side_effect=PermissionError("no")):
                migrated = write_store.read()
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(write_store.warnings[-1].code, "state_migration_write_failed")

    def test_recovery_and_parent_fsync_failures_do_not_hide_original_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("broken", encoding="utf-8")
            store = AtomicJSONStore(path)
            with mock.patch("fusion_reader_v2.services.persistence.os.replace", side_effect=PermissionError("no")):
                self.assertEqual(store.read(), {})
            self.assertIn("could not preserve original", store.warnings[-1].detail)

            with mock.patch("fusion_reader_v2.services.persistence.os.open", side_effect=PermissionError("no")):
                store._fsync_parent()

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
