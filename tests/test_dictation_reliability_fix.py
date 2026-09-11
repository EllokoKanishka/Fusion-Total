from __future__ import annotations

import json
import unittest
from unittest import mock

from fusion_reader_v2 import conversation, interpret_dictation_transcript
from fusion_reader_v2.dictation_assistant import DictationAssistant
from fusion_reader_v2.transcript_correction import CorrectionOutcome
from tests.helpers import test_app


class _Response:
    def __init__(self, payload: object) -> None:
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class DictationReliabilityFixTests(unittest.TestCase):
    def test_proofread_is_a_first_class_wake_command(self) -> None:
        cases = {
            "Lucy, corregí el texto": "all",
            "Lucy, revisá todo el borrador": "all",
            "Lucy, arreglá la selección": "selection",
            "Lucy, corregí este párrafo": "current_paragraph",
            "Lucy, revisá el último párrafo": "last_paragraph",
            "Lucy, corregí el párrafo anterior": "previous_paragraph",
        }
        for utterance, scope in cases.items():
            with self.subTest(utterance=utterance):
                instruction = interpret_dictation_transcript(utterance, require_wake_word=True)
                self.assertEqual((instruction.kind, instruction.scope), ("proofread", scope))

    def test_assistant_accepts_one_schema_valid_object_inside_chatter(self) -> None:
        instruction, detail = DictationAssistant._parse_instruction(
            'Claro. {"kind":"proofread","text":"","target":"","scope":"all","number":0,"all_matches":false} Listo.'
        )
        self.assertEqual(detail, "")
        self.assertIsNotNone(instruction)
        self.assertEqual((instruction.kind, instruction.scope), ("proofread", "all"))
        invalid, reason = DictationAssistant._parse_instruction(
            '{"kind":"proofread","text":"reescritura","target":"","scope":"all","number":0,"all_matches":false}'
        )
        self.assertIsNone(invalid)
        self.assertEqual(reason, "assistant_invalid_instruction")

    def test_ollama_structured_output_forces_temperature_zero(self) -> None:
        provider = conversation.OllamaChatProvider(base_url="http://local", default_model="qwen3:14b-q8_0")
        captured: dict = {}

        def respond(request, timeout=0):
            captured.update(json.loads(request.data.decode("utf-8")))
            return _Response({"message": {"content": '{"kind":"noop"}'}})

        schema = {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["noop"]}},
            "required": ["kind"],
            "additionalProperties": False,
        }
        with mock.patch.object(conversation.urllib.request, "urlopen", side_effect=respond):
            result = provider.chat_structured([{"role": "user", "content": "orden"}], schema=schema, think=False)
        self.assertTrue(result.ok)
        self.assertEqual(captured["options"]["temperature"], 0.0)

    def test_proofread_preserves_safe_rejections_and_paragraph_breaks(self) -> None:
        class Corrector:
            model = "qwen3:14b-q8_0"

            def health(self):
                return {"ok": True, "model": self.model, "detail": "ready"}

            def correct(self, text, **_kwargs):
                if text == "hola mundo":
                    return CorrectionOutcome("Hola, mundo.", True, True, "accepted", 11, self.model)
                return CorrectionOutcome(text, False, False, "rewrite_risk", 7, self.model)

        app = test_app()
        app._dictation_corrector = Corrector()
        result = app.dictation_proofread("hola mundo\n\nno me reescribas")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "Hola, mundo.\n\nno me reescribas")
        self.assertEqual(result["processed_parts"], 2)
        self.assertEqual(result["accepted_parts"], 1)
        self.assertEqual(result["changed_parts"], 1)
        self.assertEqual(result["rejected_parts"], 1)
        self.assertEqual(result["warning"], "dictation_proofread_partial")

    def test_proofread_transport_failure_is_atomic(self) -> None:
        class FailingCorrector:
            model = "qwen3:14b-q8_0"

            def health(self):
                return {"ok": True, "model": self.model}

            def correct(self, text, **_kwargs):
                return CorrectionOutcome(text, False, False, "connection_refused", 5, self.model)

        app = test_app()
        app._dictation_corrector = FailingCorrector()
        source = "No cambies este texto si falla el modelo."
        result = app.dictation_proofread(source)
        self.assertFalse(result["ok"])
        self.assertEqual(result["text"], source)
        self.assertEqual(result["technical_detail"], "connection_refused")

    def test_proofread_fails_closed_when_model_is_unavailable_or_input_is_unbounded(self) -> None:
        class DownCorrector:
            model = "qwen3:14b-q8_0"

            def health(self):
                return {"ok": False, "model": self.model, "detail": "ollama_down"}

        app = test_app()
        app._dictation_corrector = DownCorrector()
        self.assertEqual(app.dictation_proofread("texto")["error"], "dictation_proofread_unavailable")
        self.assertEqual(app.dictation_proofread("x" * 12_001)["error"], "dictation_proofread_too_large")
        self.assertEqual(app.dictation_proofread("   ")["error"], "empty_dictation_proofread")

    def test_fusionctl_start_path_ensures_gpu_services_instead_of_only_selecting_dead_urls(self) -> None:
        script = open("scripts/start_fusion_reader_v2.sh", encoding="utf-8").read()
        self.assertIn("start_reader_neural_tts_gpu_5090.sh", script)
        self.assertIn("start_reader_neural_tts.sh", script)
        self.assertIn("wait_until_tts_ready", script)
        self.assertIn("ensure_fusion_tts_url", script)
        self.assertIn("Fusion arrancará sin TTS operativo", script)
        self.assertIn("start_fusion_reader_v2_stt.sh", script)
        self.assertIn("ensure_fusion_gpu_stt", script)
        self.assertIn("wait_until_stt_ready", script)
        self.assertIn("Whisper large-v3-turbo en CUDA/float16", script)


if __name__ == "__main__":
    unittest.main()
