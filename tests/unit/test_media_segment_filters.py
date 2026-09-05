import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class MediaSegmentFilterTests(unittest.TestCase):
    def test_accented_artifacts_are_removed_without_rewriting_valid_segments(self):
        fake_whisper = SimpleNamespace(WhisperModel=mock.Mock(return_value=object()))
        with mock.patch.dict("sys.modules", {"faster_whisper": fake_whisper}):
            server = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/fusion_reader_v2_stt_server.py"))
        valid = "La filosofía también estudia la técnica."
        texts = [
            valid,
            "Subtítulos realizados por la comunidad de Amara.org",
            "SUBTÍTULOS POR LA COMUNIDAD DE AMARA.ORG",
            "Suscríbete al canal",
            "Suscri\u0301bete al canal",
            "El próximo tema es Spinoza.",
        ]
        segments = [SimpleNamespace(text=text, start=i, end=i + 1) for i, text in enumerate(texts)]
        text, payload = server["_segment_payload"](segments)
        self.assertEqual(text, f"{valid} El próximo tema es Spinoza.")
        self.assertEqual([item["start"] for item in payload], [0, 5])
        self.assertEqual(payload[0]["text"], valid)
