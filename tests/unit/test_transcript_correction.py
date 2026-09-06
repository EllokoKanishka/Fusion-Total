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

    def test_accepts_unchanged_text(self) -> None:
        source = "Hari Seldon llegó a Trantor."
        self.assertEqual(validate_conservative_correction(source, source), (True, "unchanged"))

    def test_rejects_empty_source_and_candidate(self) -> None:
        self.assertEqual(validate_conservative_correction("", "texto"), (False, "empty_source"))
        self.assertEqual(validate_conservative_correction("texto original", ""), (False, "empty_candidate"))

    def test_rejects_rewrites_and_explanations(self) -> None:
        source = "Hari Seldon llegó a Trantor para hablar de psicohistoria con Gaal Dornick."
        accepted, detail = validate_conservative_correction(
            source,
            "Texto corregido: Seldon visitó la capital imperial y explicó extensamente su nueva teoría matemática.",
        )
        self.assertFalse(accepted)
        self.assertIn(detail, {"explanatory_output", "rewrite_risk", "word_count_delta"})

    def test_rejects_markdown_protocol_echo_and_large_word_delta(self) -> None:
        source = "Hari Seldon llegó a Trantor para hablar de psicohistoria."
        self.assertEqual(
            validate_conservative_correction(source, "```\nHari Seldon llegó a Trantor.\n```"),
            (False, "explanatory_output"),
        )
        self.assertEqual(
            validate_conservative_correction(source, "<transcript>Hari Seldon llegó a Trantor.</transcript>"),
            (False, "protocol_echo"),
        )
        accepted, detail = validate_conservative_correction(
            source,
            "Hari Seldon llegó a Trantor y después explicó con muchísimo detalle toda la historia completa del Imperio Galáctico.",
        )
        self.assertFalse(accepted)
        self.assertEqual(detail, "word_count_delta")

    def test_rejects_character_length_and_low_similarity(self) -> None:
        source = "abcdefghij klmnopqrst uvwxyzabcd efghijklmn opqrstuvwx yzabcdefgh ijklmnopqr stuvwxyzab cdefghijkl"
        accepted, detail = validate_conservative_correction(
            source,
            "abcdefghij klmnopqrst uvwxyzabcd efghijklmn opqrstuvwx yzabcdefgh ijklmnopqr stuvwxyzab c",
        )
        self.assertFalse(accepted)
        self.assertIn(detail, {"character_length_delta", "rewrite_risk"})

        source = "Hari Seldon llegó a Trantor para hablar de la nueva ciencia matemática galáctica."
        candidate = "Gaal Dornick partió de Terminus para estudiar otra antigua disciplina filosófica imperial."
        accepted, detail = validate_conservative_correction(source, candidate)
        self.assertFalse(accepted)
        self.assertEqual(detail, "rewrite_risk")

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

    def test_correct_empty_source_and_transport_failure_preserve_original(self) -> None:
        corrector = OllamaTranscriptCorrector(model="qwen3:14b-q8_0")
        empty = corrector.correct("  ")
        self.assertFalse(empty.accepted)
        self.assertEqual(empty.detail, "empty_source")

        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            failed = corrector.correct("Harry Seldon llegó a Trantor.")
        self.assertFalse(failed.accepted)
        self.assertEqual(failed.text, "Harry Seldon llegó a Trantor.")
        self.assertEqual(failed.detail, "offline")

    def test_correct_rewrite_candidate_falls_back_to_source(self) -> None:
        source = "Hari Seldon llegó a Trantor para hablar de psicohistoria con Gaal Dornick."
        response = {
            "message": {
                "content": "El científico visitó la capital y presentó una teoría completamente nueva ante varios colegas imperiales."
            }
        }
        corrector = OllamaTranscriptCorrector(model="qwen3:14b-q8_0")
        with patch("urllib.request.urlopen", return_value=_Response(response)):
            outcome = corrector.correct(source)
        self.assertFalse(outcome.accepted)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.text, source)

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

    def test_health_reports_missing_model_and_transport_failure(self) -> None:
        corrector = OllamaTranscriptCorrector(model="qwen3:14b-q8_0")
        with patch("urllib.request.urlopen", return_value=_Response({"models": [{"name": "qwen3.5:27b"}]})):
            missing = corrector.health()
        self.assertFalse(missing["ok"])
        self.assertFalse(missing["model_present"])
        self.assertEqual(missing["detail"], "model_not_installed")

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            failed = corrector.health()
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["detail"], "connection refused")


if __name__ == "__main__":
    unittest.main()
