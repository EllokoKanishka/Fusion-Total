from __future__ import annotations

import unittest
import threading

from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.reader import ReaderService
from fusion_reader_v2.tts import AudioArtifact


class Voice:
    voice = "female.wav"
    language = "es"


class Cache:
    def get(self, text: str, voice: str, language: str):
        return None


class ReaderServiceTests(unittest.TestCase):
    def test_navigation_is_isolated_and_runs_declared_effects_once(self) -> None:
        session = ReaderSession()
        session.load(Document("book", "Book", "one two three", ["one", "two", "three"]))
        calls: list[str] = []
        service = ReaderService(
            session,
            voice=Voice(),
            cache=Cache(),
            persist=lambda: calls.append("persist"),
            prefetch_current=lambda: calls.append("prefetch"),
            prefetch_next=lambda: calls.append("prefetch-next"),
            status=session.status,
            document_generation=lambda: 1,
            artifact_for_index=lambda generation, index, text, voice, language: AudioArtifact(True),
            play=lambda path: None,
            record_metric=lambda event, payload, text: None,
            human_tts_error=lambda detail: detail,
            tts_gate=threading.Condition(),
        )

        self.assertEqual(service.next()["current"], 2)
        self.assertEqual(service.jump(3)["current"], 3)
        self.assertEqual(service.previous()["current"], 2)
        self.assertEqual(calls, ["persist", "prefetch"] * 3)
        result = service.read_current(play=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["requested_chunk_index"], 1)
        self.assertEqual(calls[-1], "prefetch-next")


if __name__ == "__main__":
    unittest.main()
