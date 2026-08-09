from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2 import DictationAssistant, NullChatProvider
from tests.helpers import FailingChatProvider, managed_test_app


class DictationAssistantTests(unittest.TestCase):
    def test_rules_are_default_and_never_call_a_model(self) -> None:
        provider = NullChatProvider("{}")
        assistant = DictationAssistant({"local": provider})

        result = assistant.interpret("Lucy, inventá algo", draft="Un texto")

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "rules_only")
        self.assertEqual(provider.calls, [])
        self.assertTrue(assistant.status()["ready"])

    def test_local_model_returns_one_bounded_delete_from_operation(self) -> None:
        provider = NullChatProvider(
            json.dumps(
                {
                    "kind": "delete_from",
                    "text": "",
                    "target": "Buenos Aires",
                    "scope": "",
                    "number": 0,
                    "all_matches": False,
                }
            )
        )
        assistant = DictationAssistant({"local": provider}, selected="local")

        result = assistant.interpret(
            "LUCY.BORRA.BONOSAIRES.LOCES.",
            draft="Una tarde lluviosa en Buenos Aires. Lo sé.",
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.instruction.kind, result.instruction.target), ("delete_from", "Buenos Aires"))
        self.assertFalse(provider.calls[-1][2]["think"])
        prompt = "\n".join(item["content"] for item in provider.calls[-1][0])
        self.assertIn("Buenos Aires", prompt)
        self.assertIn("único objeto JSON", prompt)

    def test_selection_rewrite_is_allowed_but_unbounded_output_is_rejected(self) -> None:
        allowed = DictationAssistant._parse_instruction(
            '{"kind":"replace_selection","text":"Una versión mejor.","target":"",'
            '"scope":"","number":0,"all_matches":false}'
        )
        rejected = DictationAssistant._parse_instruction(
            '{"kind":"replace_document","text":"otro","target":"","scope":"","number":0,"all_matches":false}'
        )

        self.assertEqual(allowed[0].kind, "replace_selection")  # type: ignore[union-attr]
        self.assertIsNone(rejected[0])
        self.assertEqual(rejected[1], "assistant_disallowed_instruction")

    def test_invalid_json_and_missing_targets_fail_closed(self) -> None:
        self.assertEqual(DictationAssistant._parse_instruction("charlemos")[1], "assistant_invalid_json")
        instruction, detail = DictationAssistant._parse_instruction(
            '{"kind":"delete_from","text":"","target":"","scope":"","number":0,"all_matches":false}'
        )
        self.assertIsNone(instruction)
        self.assertEqual(detail, "assistant_missing_target")

    def test_selector_catalog_and_missing_local_model_status(self) -> None:
        class MissingModelProvider(NullChatProvider):
            default_model = "qwen3:4b"

            def health(self) -> dict:
                return {
                    "ok": True,
                    "provider": "ollama",
                    "model_present": False,
                    "detail": "ready",
                }

        assistant = DictationAssistant({"local": MissingModelProvider("{}")}, selected="unknown")
        self.assertEqual(assistant.selected, "rules")
        self.assertEqual(assistant.select(" LOCAL "), "local")
        status = assistant.status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["detail"], "model_not_installed")
        self.assertEqual(status["model"], "qwen3:4b")
        with self.assertRaisesRegex(ValueError, "invalid_dictation_assistant"):
            assistant.select("unbounded")

    def test_missing_and_failing_providers_degrade_without_an_edit(self) -> None:
        missing = DictationAssistant({"local": NullChatProvider("{}")}, selected="local")
        missing.providers.clear()
        unavailable = missing.interpret("Lucy, reescribí esto")
        self.assertFalse(unavailable.ok)
        self.assertEqual(unavailable.detail, "assistant_unavailable")

        failing = DictationAssistant({"local": FailingChatProvider("timeout")}, selected="local")
        failed = failing.interpret("Lucy, reescribí esto")
        self.assertFalse(failed.ok)
        self.assertEqual(failed.detail, "timeout")
        self.assertEqual(failed.duration_ms, 41)

    def test_parser_accepts_fences_and_rejects_malformed_fields(self) -> None:
        fenced, detail = DictationAssistant._parse_instruction(
            '```json\n{"kind":"undo","number":2,"all_matches":false}\n```'
        )
        self.assertEqual(detail, "")
        self.assertEqual(fenced.kind, "undo")  # type: ignore[union-attr]
        self.assertEqual(fenced.number, 2)  # type: ignore[union-attr]

        cases = (
            ("[]", "assistant_invalid_instruction"),
            ('{"kind":"undo","surprise":true}', "assistant_invalid_instruction"),
            ('{"kind":"insert","text":""}', "assistant_missing_text"),
            ('{"kind":"read","scope":"somewhere"}', "assistant_invalid_read_scope"),
            ('{"kind":"read","scope":"from_text"}', "assistant_missing_target"),
            ('{"kind":"undo","number":true}', "assistant_invalid_instruction"),
            ('{"kind":"undo","number":"many"}', "assistant_invalid_instruction"),
            ('{"kind":"undo","all_matches":"yes"}', "assistant_invalid_instruction"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                instruction, actual = DictationAssistant._parse_instruction(raw)
                self.assertIsNone(instruction)
                self.assertEqual(actual, expected)

        oversized = json.dumps({"kind": "insert", "text": "x" * 12_001})
        self.assertEqual(DictationAssistant._parse_instruction(oversized)[1], "assistant_instruction_too_large")

    def test_context_is_capped_before_it_reaches_the_provider(self) -> None:
        provider = NullChatProvider('{"kind":"noop","text":"","target":"","scope":"","number":0,"all_matches":false}')
        assistant = DictationAssistant({"local": provider}, selected="local", max_context_chars=1000)
        assistant.interpret("Lucy, revisá esto", draft="x" * 5000)
        prompt = "\n".join(item["content"] for item in provider.calls[-1][0])
        self.assertLess(len(prompt), 3000)

    def test_selected_assistant_survives_reader_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = DictationAssistant({"local": NullChatProvider("{}")})
            with managed_test_app(root=root, dictation_assistant=first) as app:
                self.assertTrue(app.set_dictation_assistant("local")["ok"])
            second = DictationAssistant({"local": NullChatProvider("{}")})
            with managed_test_app(root=root, dictation_assistant=second) as restored:
                self.assertEqual(restored.dictation_assistant_status()["selected"], "local")


if __name__ == "__main__":
    unittest.main()
