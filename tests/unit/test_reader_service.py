from __future__ import annotations

import unittest

from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.reader import ReaderService


class ReaderServiceTests(unittest.TestCase):
    def test_navigation_is_isolated_and_runs_declared_effects_once(self) -> None:
        session = ReaderSession()
        session.load(Document("book", "Book", "one two three", ["one", "two", "three"]))
        calls: list[str] = []
        service = ReaderService(
            session,
            persist=lambda: calls.append("persist"),
            prefetch_current=lambda: calls.append("prefetch"),
            status=session.status,
        )

        self.assertEqual(service.next()["current"], 2)
        self.assertEqual(service.jump(3)["current"], 3)
        self.assertEqual(service.previous()["current"], 2)
        self.assertEqual(calls, ["persist", "prefetch"] * 3)


if __name__ == "__main__":
    unittest.main()
