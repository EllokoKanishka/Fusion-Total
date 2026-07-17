from __future__ import annotations

import unittest
import time
from dataclasses import dataclass

from fusion_reader_v2.domain.jobs import BackgroundJob, JobRegistry, JobState


@dataclass
class Item:
    terminal: bool
    updated: float


class JobRegistryTests(unittest.TestCase):
    def _registry(self, **overrides) -> JobRegistry[Item]:
        options = {
            "max_items": 2,
            "ttl_seconds": 10.0,
            "is_terminal": lambda item: item.terminal,
            "updated_at": lambda item: item.updated,
        }
        options.update(overrides)
        return JobRegistry(**options)

    def test_common_job_contract_has_stable_states_and_fields(self) -> None:
        job = BackgroundJob("abc", "import", state=JobState.DONE, progress=100)
        payload = job.to_dict()
        self.assertEqual(payload["id"], "abc")
        self.assertEqual(payload["type"], "import")
        self.assertEqual(payload["state"], "done")
        self.assertTrue(payload["terminal"])

    def test_registry_prunes_expired_terminal_items(self) -> None:
        cleaned: list[Item] = []
        registry = self._registry(cleanup=cleaned.append)
        now = time.time()
        stale = Item(True, now)
        active = Item(False, now)
        registry.add("stale", stale)
        registry.add("active", active)
        self.assertEqual(registry.prune(now=now + 20.0), 1)
        self.assertIsNone(registry.get("stale"))
        self.assertIs(registry.get("active"), active)
        self.assertEqual(cleaned, [stale])

    def test_registry_evicts_old_terminal_before_rejecting_new_work(self) -> None:
        registry = self._registry(ttl_seconds=1000.0)
        registry.add("old", Item(True, 1.0))
        registry.add("active", Item(False, 2.0))
        registry.add("new", Item(False, 3.0))
        self.assertIsNone(registry.get("old"))
        with self.assertRaisesRegex(RuntimeError, "job_registry_full"):
            registry.add("overflow", Item(False, 4.0))

    def test_registry_validates_configuration_and_ids(self) -> None:
        with self.assertRaises(ValueError):
            self._registry(max_items=0)
        with self.assertRaises(ValueError):
            self._registry(ttl_seconds=0)
        registry = self._registry()
        with self.assertRaises(ValueError):
            registry.add("", Item(False, 1.0))

    def test_update_remove_snapshot_len_and_cleanup_contract(self) -> None:
        cleaned: list[Item] = []
        backing: dict[str, Item] = {}
        registry = self._registry(cleanup=cleaned.append, backing=backing)
        first = Item(False, time.time())
        registry.add("first", first)
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry.snapshot(), {"first": first})
        self.assertIsNone(registry.update("missing", lambda item: setattr(item, "terminal", True)))
        self.assertIs(registry.update("first", lambda item: setattr(item, "terminal", True)), first)
        self.assertTrue(first.terminal)
        self.assertIs(registry.remove("first"), first)
        self.assertEqual(cleaned, [first])
        self.assertIsNone(registry.remove("missing"))

    def test_duplicate_key_replaces_without_eviction_and_active_full_has_no_candidate(self) -> None:
        registry = self._registry(max_items=1)
        first = Item(False, 1.0)
        replacement = Item(False, 2.0)
        registry.add("same", first)
        registry.add("same", replacement)
        self.assertIs(registry.get("same"), replacement)
        with self.assertRaisesRegex(RuntimeError, "job_registry_full"):
            registry.add("other", Item(False, 3.0))


if __name__ == "__main__":
    unittest.main()
