from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fusion_reader_v2.transcript_correction import (
    OllamaTranscriptCorrector,
    validate_conservative_correction,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class TranscriptCorrectionTests(unittest.TestCase):
    def test_accepts_small_lexical_repairs(self) -> None:
        source = "Un psicohistoriador, Harry Seldon, prevé el derrumbamiento del Imperio Galáctico."
        candidate = "Un psicohistoriador, Hari Seldon, prevé el derrumbamiento del Imperio Galáctico."
        accepted, detail = validate_conservative_correction(source, candidate)
        self.assertTrue(accepted)
        self.assertEqual(detail, "accepted")

    def test_rejects_rewrites_and_explanations(self) -> None:
        source = "Hari Seldon llegó a Trantor para hablar de psicohistoria con Gaal Dornick."
        accepted, detail = validate_conservative_correction(
            source,
            "Texto corregido: Seldon visitó la capital imperial y explicó extensamente su nueva teoría matemática.",
        )
        self.assertFalse(accepted)
        self.assertIn(detail, {"explanatory_output", "rewrite_risk", "word_count_delta"})

    def test_ollama_request_forces_thinking_off_and_temperature_zero(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _Response({"message": {"content": "Hari Seldon llegó a Trantor."}})

        corrector = OllamaTranscriptCorrector(
            base_url="http://127.0.0.1:11434",
            model="qwen3:14b-q8_0",
            timeout_seconds=9,
            keep_alive="1m",
        )
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            outcome = corrector.correct(
                "Harry Seldon llegó a Trantor.",
                context="Audiolibro de Fundación.",
                glossary="Hari Seldon, Trantor",
            )

        self.assertTrue(outcome.accepted)
        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.text, "Hari Seldon llegó a Trantor.")
        payload = captured["payload"]
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["temperature"], 0.0)
        self.assertEqual(payload["model"], "qwen3:14b-q8_0")
        self.assertEqual(payload["keep_alive"], "1m")
        user_data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(user_data["glossary"], "Hari Seldon, Trantor")
        self.assertEqual(captured["timeout"], 9)

    def test_health_requires_the_configured_local_model(self) -> None:
        corrector = OllamaTranscriptCorrector(model="qwen3:14b-q8_0")
        payload = {
            "models": [
                {"name": "qwen3:14b-q8_0"},
                {"name": "qwen3.5:27b"},
            ]
        }
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            health = corrector.health()
        self.assertTrue(health["ok"])
        self.assertTrue(health["model_present"])
        self.assertFalse(health["thinking"])
        self.assertEqual(health["temperature"], 0.0)


if __name__ == "__main__":
    unittest.main()
