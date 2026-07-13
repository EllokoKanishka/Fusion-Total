from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.audio_export import AudioExportService


class Voice:
    voice = "female.wav"
    language = "es"


class Cache:
    def get(self, text: str, voice: str, language: str):
        return None


class TTS:
    def health(self) -> dict:
        return {"ok": True}


class AudioExportServiceTests(unittest.TestCase):
    def test_resolves_snapshot_without_facade(self) -> None:
        session = ReaderSession()
        session.load(Document("book", "Book", "one two", ["one", "two"]))
        lock = threading.RLock()
        with tempfile.TemporaryDirectory() as tmp:
            service = AudioExportService(
                session=session,
                voice=Voice(),
                cache=Cache(),
                tts=TTS(),
                output_root=Path(tmp),
                background_condition=threading.Condition(lock),
                background_is_open_locked=lambda: True,
                before_registration=lambda: None,
                wait_for_interactive_tts=lambda: None,
                synthesize=lambda text, voice, language: None,  # type: ignore[arg-type,return-value]
            )
            snapshot = service.resolve_snapshot("full")
            self.assertEqual(snapshot.blocks, [(1, "one"), (2, "two")])
            self.assertEqual(snapshot.voice, "female.wav")


if __name__ == "__main__":
    unittest.main()
