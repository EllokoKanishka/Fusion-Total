from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fusion_reader_v2.metrics import VoiceMetricsStore
from fusion_reader_v2.services.audio import AudioService, KNOWN_FUSION_VOICES
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

    def test_catalog_exposes_known_voices_while_tts_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            voice = SimpleNamespace(voice="female_07.wav", language="es")
            tts = NullTTSProvider()
            tts.health = mock.Mock(return_value={"ok": False, "detail": "tts_owner_missing"})
            tts.voices = mock.Mock(return_value=[])
            service = AudioService(
                tts=tts,
                voice=voice,
                metrics=VoiceMetricsStore(Path(tmp) / "metrics.jsonl"),
                synthesize=lambda _text: AudioArtifact(False),
                play=lambda _path: None,
                record_metric=lambda _event, _payload, _text: None,
                persist=lambda: None,
                clear_prefetch=lambda: None,
                prepare_status=lambda: {"status": "idle"},
                cancel_prepare=lambda: {"ok": True},
                status=lambda: {"ok": True, "voice": voice.voice},
            )

            catalog = service.catalog(fallback_current=True)

            self.assertEqual(catalog["voices"], list(KNOWN_FUSION_VOICES))
            self.assertEqual(len(catalog["voices"]), 20)
            self.assertEqual(catalog["source"], "known_fallback")
            self.assertFalse(catalog["tts_ready"])
            self.assertEqual(catalog["detail"], "tts_owner_missing")

    def test_catalog_keeps_provider_as_authority_when_it_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            voice = SimpleNamespace(voice="custom.wav", language="es")
            tts = NullTTSProvider()
            tts.health = mock.Mock(return_value={"ok": True, "detail": "Ready"})
            tts.voices = mock.Mock(return_value=["custom.wav"])
            service = AudioService(
                tts=tts,
                voice=voice,
                metrics=VoiceMetricsStore(Path(tmp) / "metrics.jsonl"),
                synthesize=lambda _text: AudioArtifact(True),
                play=lambda _path: None,
                record_metric=lambda _event, _payload, _text: None,
                persist=lambda: None,
                clear_prefetch=lambda: None,
                prepare_status=lambda: {"status": "idle"},
                cancel_prepare=lambda: {"ok": True},
                status=lambda: {"ok": True, "voice": voice.voice},
            )

            catalog = service.catalog(fallback_current=True)

            self.assertEqual(catalog["voices"], ["custom.wav"])
            self.assertEqual(catalog["source"], "provider")
            self.assertTrue(catalog["tts_ready"])


if __name__ == "__main__":
    unittest.main()
