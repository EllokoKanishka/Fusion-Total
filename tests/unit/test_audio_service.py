from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fusion_reader_v2.metrics import VoiceMetricsStore
from fusion_reader_v2.services.audio import AudioService
from fusion_reader_v2.tts import AudioArtifact, NullTTSProvider


class AudioServiceTests(unittest.TestCase):
    def test_catalog_selection_and_test_use_explicit_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            voice = SimpleNamespace(voice="old.wav", language="es")
            tts = NullTTSProvider()
            tts.voices = mock.Mock(return_value=["old.wav", "new.wav"])
            calls: list[str] = []
            service = AudioService(
                tts=tts,
                voice=voice,
                metrics=VoiceMetricsStore(Path(tmp) / "metrics.jsonl"),
                synthesize=lambda _text: AudioArtifact(True, provider="synthetic", cached=True),
                play=lambda _path: calls.append("play"),
                record_metric=lambda event, _payload, _text: calls.append(event),
                persist=lambda: calls.append("persist"),
                clear_prefetch=lambda: calls.append("clear"),
                prepare_status=lambda: {"status": "idle"},
                cancel_prepare=lambda: {"ok": True},
                status=lambda: {"ok": True, "voice": voice.voice},
            )
            self.assertEqual(service.catalog()["current"], "old.wav")
            self.assertTrue(service.test("hola", play=True)["ok"])
            self.assertEqual(service.set_voice("new.wav")["voice"], "new.wav")
            self.assertEqual(calls, ["play", "voice_test", "persist", "clear"])
            self.assertFalse(hasattr(service, "owner"))


if __name__ == "__main__":
    unittest.main()
