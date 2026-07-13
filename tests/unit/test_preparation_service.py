from __future__ import annotations

import threading
import time
import unittest

from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.preparation import PreparationService
from fusion_reader_v2.tts import AudioArtifact


class Voice:
    voice = "female.wav"
    language = "es"


class Cache:
    def get(self, text: str, voice: str, language: str):
        return None


class TTS:
    def health(self) -> dict:
        return {"ok": True}


class PreparationServiceTests(unittest.TestCase):
    def test_service_prepares_document_without_facade(self) -> None:
        session = ReaderSession()
        session.load(Document("book", "Book", "one two", ["one", "two"]))
        condition = threading.Condition(threading.RLock())
        service = PreparationService(
            session=session,
            voice=Voice(),
            cache=Cache(),
            tts=TTS(),
            background_condition=condition,
            background_is_open_locked=lambda: True,
            document_generation=lambda: 1,
            before_registration=lambda: None,
            synthesize=lambda text, voice, language: AudioArtifact(True),
            human_error=lambda detail: detail,
        )
        service.start()
        deadline = time.monotonic() + 2
        while service.status()["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(service.status()["status"], "done")
        self.assertEqual(service.status()["generated"], 2)


if __name__ == "__main__":
    unittest.main()
