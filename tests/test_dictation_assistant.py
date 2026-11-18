from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fusion_reader_v2 import DictationAssistant, NullChatProvider
from fusion_reader_v2.config import create_settings
from fusion_reader_v2.web.context import WebContext
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

    def test_prose_revision_uses_the_selected_model_and_preserves_paragraphs(self) -> None:
        original = "La casa era blanco blanco.\n\nNo había 3 plantas junto a la mesa de «Ana»."
        revised = "La casa era blanca.\n\nJunto a la mesa de «Ana» no había 3 plantas."
        cloud = NullChatProvider(json.dumps({"text": revised}, ensure_ascii=False))
        local = NullChatProvider("{}")
        assistant = DictationAssistant({"openai": cloud, "local": local}, selected="openai")

        outcome = assistant.revise_prose(original)

        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["text"], revised)
        self.assertEqual(outcome["changed_parts"], 1)
        self.assertEqual(local.calls, [])
        self.assertEqual(len(cloud.calls), 1)

    def test_prose_revision_rejects_changed_facts_and_respects_rules_mode(self) -> None:
        original = "En el patio había 17 plantas."
        provider = NullChatProvider(json.dumps({"text": "En el patio había 20 plantas."}))
        assistant = DictationAssistant({"local": provider})
        self.assertFalse(assistant.revise_prose(original)["ok"])
        self.assertEqual(provider.calls, [])

        assistant.select("local")
        outcome = assistant.revise_prose(original)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["technical_detail"], "protected_numbers_changed")
        self.assertNotIn("text", outcome)

    def test_prose_revision_splits_long_requests_at_text_boundaries(self) -> None:
        from fusion_reader_v2.conversation import ChatResult

        class RevisingProvider(NullChatProvider):
            def __init__(self):
                super().__init__("")
                self.fragments = []

            def chat(self, messages, **kwargs):
                fragment = json.loads(messages[-1]["content"])["fragmento"]
                self.fragments.append(fragment)
                return ChatResult(True, answer=json.dumps({"text": fragment.replace("blanca blanca", "blanca")}))

        text = ("La casa era blanca blanca. En el patio había plantas. " * 65).strip() + "\n\nOtro párrafo."
        provider = RevisingProvider()
        assistant = DictationAssistant({"local": provider}, selected="local")
        outcome = assistant.revise_prose(text)

        self.assertTrue(outcome["ok"])
        self.assertGreater(len(provider.fragments), 1)
        self.assertTrue(all(len(fragment) <= 2_400 for fragment in provider.fragments))
        self.assertEqual(outcome["text"], text.replace("blanca blanca", "blanca"))

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

    def test_structured_provider_receives_the_bounded_instruction_schema(self) -> None:
        class StructuredProvider(NullChatProvider):
            def chat_structured(self, messages, *, schema, **kwargs):
                self.schema = schema
                self.kwargs = kwargs
                return super().chat(messages, think=kwargs.get("think"), num_predict=kwargs.get("num_predict"))

        provider = StructuredProvider('{"kind":"noop","text":"","target":"","scope":"","number":0,"all_matches":false}')
        assistant = DictationAssistant({"local": provider}, selected="local")

        result = assistant.interpret("Lucy, hacé algo prudente", draft="Texto")

        self.assertTrue(result.ok)
        self.assertFalse(provider.kwargs["think"])
        self.assertEqual(provider.kwargs["keep_alive"], "10m")
        self.assertFalse(provider.schema["additionalProperties"])
        self.assertIn("delete_last_words", provider.schema["properties"]["kind"]["enum"])
        self.assertIn("replace_last_words", provider.schema["properties"]["kind"]["enum"])

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
            ('{"kind":"delete_last_words","number":0}', "assistant_invalid_word_count"),
            ('{"kind":"replace_last_words","number":20,"text":""}', "assistant_missing_text"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                instruction, actual = DictationAssistant._parse_instruction(raw)
                self.assertIsNone(instruction)
                self.assertEqual(actual, expected)

        oversized = json.dumps({"kind": "insert", "text": "x" * 12_001})
        self.assertEqual(DictationAssistant._parse_instruction(oversized)[1], "assistant_instruction_too_large")

    def test_edits_use_exact_anchors_and_browser_offsets(self) -> None:
        response = json.dumps(
            {
                "kind": "edit",
                "edits": [
                    {"before": "analisis", "after": "análisis"},
                    {"before": "filosofia", "after": "filosofía"},
                ],
            }
        )
        provider = NullChatProvider(response)
        assistant = DictationAssistant({"local": provider}, selected="local")
        result = assistant.interpret("Lucy, poné las tildes", draft="😀 filosofia, analisis.")
        self.assertTrue(result.ok)
        self.assertEqual(
            [(e.start, e.end, e.after) for e in result.instruction.edits], [(3, 12, "filosofía"), (14, 22, "análisis")]
        )

    def test_ambiguous_and_overlapping_anchors_are_rejected_atomically(self) -> None:
        def parse(draft, pairs, start=0, end=0):
            raw = json.dumps({"kind": "edit", "edits": [{"before": a, "after": b} for a, b in pairs]})
            return DictationAssistant._parse_instruction(raw, draft, start, end)

        self.assertEqual(parse("azul azul", [("azul", "verde")]), (None, "assistant_edit_anchor_ambiguous"))
        self.assertEqual(parse("rojo azul", [("rojo", "gris"), ("verde", "")]), (None, "assistant_edit_anchor_missing"))
        self.assertEqual(parse("rojo azul", [("rojo", "gris"), ("rojo azul", "")]), (None, "assistant_edit_overlap"))
        selected, detail = parse("azul azul", [("azul", "verde")], 5, 9)
        self.assertEqual(detail, "")
        self.assertEqual(selected.edits[0].start, 5)

    def test_reviews_invalid_join_once_before_applying(self) -> None:
        from fusion_reader_v2.conversation import ChatResult

        class ReviewingProvider(NullChatProvider):
            def chat(self, messages, **_kwargs):
                if len(messages) == 2:
                    return ChatResult(True, answer='{"kind":"edit","edits":[{"before":" azul","after":""}]}')
                return ChatResult(True, answer='{"kind":"edit","edits":[{"before":" y azul","after":""}]}')

        assistant = DictationAssistant({"local": ReviewingProvider("")}, selected="local")
        result = assistant.interpret("Sacá el segundo color", draft="Era verde y azul.")
        self.assertTrue(result.ok)
        self.assertEqual(result.instruction.edits[0].before, " y azul")

    def test_cloud_dictation_uses_dialogue_model_unless_overridden(self) -> None:
        selected = {"FUSION_READER_OPENAI_CHAT_MODEL": "openai/modelo-elegido"}
        self.assertEqual(create_settings(environ=selected).providers.openai_dictation_model, "openai/modelo-elegido")
        selected["FUSION_READER_OPENAI_DICTATION_MODEL"] = "openai/otro"
        self.assertEqual(create_settings(environ=selected).providers.openai_dictation_model, "openai/otro")

    def test_context_is_capped_before_it_reaches_the_provider(self) -> None:
        provider = NullChatProvider('{"kind":"noop","text":"","target":"","scope":"","number":0,"all_matches":false}')
        assistant = DictationAssistant({"local": provider}, selected="local", max_context_chars=1000)
        assistant.interpret("Lucy, revisá esto", draft="x" * 5000)
        context = json.loads(provider.calls[-1][0][-1]["content"])
        self.assertEqual(len(context["borrador"]), 1000)

    def test_selected_assistant_survives_reader_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = DictationAssistant({"local": NullChatProvider("{}")})
            with managed_test_app(root=root, dictation_assistant=first) as app:
                self.assertTrue(app.set_dictation_assistant("local")["ok"])
            second = DictationAssistant({"local": NullChatProvider("{}")})
            with managed_test_app(root=root, dictation_assistant=second) as restored:
                self.assertEqual(restored.dictation_assistant_status()["selected"], "local")

    def test_local_model_install_job_is_owned_and_makes_provider_ready(self) -> None:
        class InstallableProvider(NullChatProvider):
            default_model = "qwen3:4b"

            def __init__(self) -> None:
                super().__init__("{}")
                self.installed = False

            def health(self) -> dict:
                return {
                    "ok": True,
                    "provider": "ollama",
                    "model": self.default_model,
                    "model_present": self.installed,
                }

            def install_model(self, model: str = "", *, cancel_event=None) -> dict:
                self.installed = model == self.default_model and not cancel_event.is_set()
                return {"ok": self.installed, "model": model, "detail": "installed"}

            def preload_model(self, model: str = "", *, keep_alive="10m") -> dict:
                return {
                    "ok": self.installed and model == self.default_model,
                    "model": model,
                    "keep_alive": keep_alive,
                    "load_duration_ms": 123,
                    "duration_ms": 150,
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = InstallableProvider()
            assistant = DictationAssistant({"local": provider}, selected="local")
            with managed_test_app(root=root, dictation_assistant=assistant) as app:
                settings = create_settings(environ={"HOME": tmp}, repository_root=root)
                context = WebContext(app=app, settings=settings, runtime_info={})
                started = context.start_dictation_model_install()
                self.assertIn(started["state"], {"queued", "running", "done"})
                deadline = time.monotonic() + 2
                status = context.dictation_model_install_status()
                while not status.get("terminal") and time.monotonic() < deadline:
                    time.sleep(0.01)
                    status = context.dictation_model_install_status()
                self.assertEqual(status["state"], "done")
                self.assertTrue(app.dictation_assistant_status()["ready"])
                warmed = context.warm_dictation_model()
                self.assertEqual(warmed["state"], "ready")
                self.assertEqual(warmed["load_duration_ms"], 123)
                self.assertGreater(warmed["ready_until_ts"], time.time())
                self.assertTrue(context.shutdown_jobs(timeout=2)["ok"])

    def test_model_installations_are_serialized_across_provider_switches(self) -> None:
        class BlockingProvider(NullChatProvider):
            def __init__(self, model: str) -> None:
                super().__init__("{}")
                self.default_model = model
                self.started = threading.Event()
                self.release = threading.Event()
                self.install_calls = 0

            def health(self) -> dict:
                return {
                    "ok": True,
                    "provider": "ollama",
                    "model": self.default_model,
                    "model_present": False,
                }

            def install_model(self, model: str = "", *, cancel_event=None) -> dict:
                self.install_calls += 1
                self.started.set()
                self.release.wait(2)
                return {
                    "ok": not cancel_event.is_set(),
                    "model": model,
                    "detail": "installed",
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local4b = BlockingProvider("qwen3:4b")
            local14b = BlockingProvider("qwen3:14b-q8_0")
            assistant = DictationAssistant(
                {"local": local4b, "local14b": local14b},
                selected="local",
            )
            with managed_test_app(root=root, dictation_assistant=assistant) as app:
                settings = create_settings(environ={"HOME": tmp}, repository_root=root)
                context = WebContext(app=app, settings=settings, runtime_info={})
                first = context.start_dictation_model_install()
                self.assertIn(first["state"], {"queued", "running"})
                self.assertTrue(local4b.started.wait(1))

                assistant.select("local14b")
                second = context.start_dictation_model_install()

                self.assertEqual(second["model"], "qwen3:4b")
                self.assertIn(second["state"], {"queued", "running"})
                self.assertEqual(local4b.install_calls, 1)
                self.assertEqual(local14b.install_calls, 0)

                local4b.release.set()
                self.assertTrue(context.shutdown_jobs(timeout=2)["ok"])

    def test_local_model_warmup_fails_closed_and_expires(self) -> None:
        class WarmProvider(NullChatProvider):
            default_model = "qwen3:4b"

            def __init__(self, *, healthy=True, preload_ok=True) -> None:
                super().__init__("{}")
                self.healthy = healthy
                self.preload_ok = preload_ok
                self.preload_calls = 0

            def health(self) -> dict:
                return {
                    "ok": self.healthy,
                    "provider": "ollama",
                    "model_present": self.healthy,
                    "model_resident": self.healthy,
                }

            def preload_model(self, model: str = "", *, keep_alive="10m") -> dict:
                self.preload_calls += 1
                return {"ok": self.preload_ok, "model": model, "detail": "warm_failed"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = create_settings(environ={"HOME": tmp}, repository_root=root)

            rules = DictationAssistant({"local": WarmProvider()})
            with managed_test_app(root=root / "rules", dictation_assistant=rules) as app:
                context = WebContext(app=app, settings=settings, runtime_info={})
                self.assertEqual(context.warm_dictation_model()["state"], "not_required")

            no_preload = DictationAssistant({"local": NullChatProvider("{}")}, selected="local")
            with managed_test_app(root=root / "missing", dictation_assistant=no_preload) as app:
                context = WebContext(app=app, settings=settings, runtime_info={})
                self.assertEqual(context.warm_dictation_model()["state"], "error")

            unhealthy = DictationAssistant({"local": WarmProvider(healthy=False)}, selected="local")
            with managed_test_app(root=root / "unhealthy", dictation_assistant=unhealthy) as app:
                context = WebContext(app=app, settings=settings, runtime_info={})
                self.assertEqual(context.warm_dictation_model()["state"], "error")

            provider = WarmProvider(preload_ok=False)
            failed = DictationAssistant({"local": provider}, selected="local")
            with managed_test_app(root=root / "failed", dictation_assistant=failed) as app:
                context = WebContext(app=app, settings=settings, runtime_info={})
                self.assertEqual(context.warm_dictation_model()["state"], "error")

            provider = WarmProvider()
            ready = DictationAssistant({"local": provider}, selected="local")
            with managed_test_app(root=root / "ready", dictation_assistant=ready) as app:
                context = WebContext(app=app, settings=settings, runtime_info={})
                self.assertEqual(context.warm_dictation_model()["state"], "ready")
                self.assertEqual(context.warm_dictation_model()["state"], "ready")
                self.assertEqual(provider.preload_calls, 1)
                context._dictation_model_warm["ready_until_ts"] = 0
                self.assertEqual(context.dictation_model_warm_status()["state"], "cold")

    def test_local14b_catalog_persistence_and_warmup_isolation(self) -> None:
        class DummyProvider(NullChatProvider):
            def __init__(self, model: str) -> None:
                super().__init__("{}")
                self.default_model = model

            def health(self) -> dict:
                return {"ok": True, "provider": "ollama", "model": self.default_model, "model_present": True}

            def preload_model(self, model: str = "", *, keep_alive="10m") -> dict:
                return {"ok": True, "model": model, "duration_ms": 100, "load_duration_ms": 80}

        local4b = DummyProvider("qwen3:4b")
        local14b = DummyProvider("qwen3:14b-q8_0")
        openai_p = DummyProvider("openai/gpt-5-nano")

        assistant = DictationAssistant(
            {"local": local4b, "local14b": local14b, "openai": openai_p},
            selected="rules",
        )

        catalog = assistant.catalog()
        ids = [item["id"] for item in catalog]
        self.assertEqual(ids, ["rules", "local", "local14b", "openai"])

        local14b_item = next(item for item in catalog if item["id"] == "local14b")
        self.assertEqual(local14b_item["model"], "qwen3:14b-q8_0")
        self.assertIn("14B", local14b_item["label"])

        self.assertEqual(assistant.select("local14b"), "local14b")
        self.assertEqual(assistant.status()["selected"], "local14b")
        self.assertEqual(assistant.status()["model"], "qwen3:14b-q8_0")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with managed_test_app(root=root, dictation_assistant=assistant) as app:
                self.assertTrue(app.set_dictation_assistant("local14b")["ok"])

            restored = DictationAssistant({"local": local4b, "local14b": local14b, "openai": openai_p})
            with managed_test_app(root=root, dictation_assistant=restored) as app:
                self.assertEqual(app.dictation_assistant_status()["selected"], "local14b")

                settings = create_settings(environ={"HOME": tmp}, repository_root=root)
                context = WebContext(app=app, settings=settings, runtime_info={})

                warmed = context.warm_dictation_model()
                self.assertEqual(warmed["state"], "ready")
                self.assertEqual(warmed["model"], "qwen3:14b-q8_0")

                app.set_dictation_assistant("local")
                warmed_4b = context.warm_dictation_model()
                self.assertEqual(warmed_4b["state"], "ready")
                self.assertEqual(warmed_4b["model"], "qwen3:4b")


if __name__ == "__main__":
    unittest.main()
