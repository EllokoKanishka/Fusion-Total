from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import (
    ConversationCore,
    NullChatProvider,
    OpenClawChatProvider,
    SelectableChatProvider,
)
from fusion_reader_v2.config import ConfigurationError, create_settings
from fusion_reader_v2.web.context import WebContext
from scripts import setup_fusion_openai_dialogue
from tests.helpers import close_test_app, test_app


class OpenAIProviderTests(unittest.TestCase):
    def test_openclaw_provider_uses_isolated_agent_and_parses_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = Path(tmp) / "openclaw"
            command.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            prompt_seen = ""

            def fake_run(cmd, **kwargs):
                nonlocal prompt_seen
                prompt_path = Path(cmd[cmd.index("--message-file") + 1])
                prompt_seen = prompt_path.read_text(encoding="utf-8")
                payload = {
                    "result": {
                        "payloads": [{"text": "Respuesta desde OpenAI."}],
                        "meta": {"agentMeta": {"model": "gpt-5.6-sol"}},
                    }
                }
                return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

            provider = OpenClawChatProvider(
                command=str(command),
                agent="fusion-dialogue",
                default_model="openai/gpt-5.6-sol",
                environment={"PATH": ""},
            )
            with mock.patch("fusion_reader_v2.conversation.run_owned", side_effect=fake_run) as run:
                result = provider.chat(
                    [{"role": "system", "content": "Sos Lucy."}, {"role": "user", "content": "Hola"}],
                    think=True,
                )

            self.assertTrue(result.ok, result.detail)
            self.assertEqual(result.answer, "Respuesta desde OpenAI.")
            self.assertEqual(result.model, "gpt-5.6-sol")
            command_line = run.call_args.args[0]
            self.assertIn("--local", command_line)
            self.assertEqual(command_line[command_line.index("--agent") + 1], "fusion-dialogue")
            self.assertEqual(command_line[command_line.index("--model") + 1], "openai/gpt-5.6-sol")
            self.assertIn("No uses herramientas", prompt_seen)
            self.assertIn("<SYSTEM>\nSos Lucy.\n</SYSTEM>", prompt_seen)
            self.assertIn("<USER>\nHola\n</USER>", prompt_seen)

    def test_openclaw_provider_parses_new_root_payload_format(self) -> None:
        payload = {
            "payloads": [{"text": "Respuesta desde OpenClaw nuevo."}],
            "meta": {"agentMeta": {"model": "gpt-5.6-sol"}},
            "stopReason": "stop",
        }

        answer, model, detail = OpenClawChatProvider._extract_answer(json.dumps(payload))

        self.assertEqual(answer, "Respuesta desde OpenClaw nuevo.")
        self.assertEqual(model, "gpt-5.6-sol")
        self.assertEqual(detail, "")

    def test_openclaw_provider_fails_closed_when_agent_is_not_isolated(self) -> None:
        provider = OpenClawChatProvider(command="openclaw", agent="main")
        self.assertFalse(provider.available())
        self.assertEqual(provider.chat([{"role": "user", "content": "Hola"}]).detail, "openclaw_unavailable")

    def test_selectable_provider_switches_without_leaking_local_model_override(self) -> None:
        local = NullChatProvider("local")
        cloud = NullChatProvider("cloud")
        cloud.default_model = "openai/gpt-5.6-sol"
        provider = SelectableChatProvider({"local": local, "openai": cloud})

        local_result = provider.chat([{"role": "user", "content": "uno"}], model="qwen3:14b-q8_0")
        provider.select("openai")
        cloud_result = provider.chat([{"role": "user", "content": "dos"}], model="qwen3:14b-q8_0")

        self.assertEqual(local_result.answer, "local")
        self.assertEqual(cloud_result.answer, "cloud")
        self.assertEqual(local.calls[-1][1], "qwen3:14b-q8_0")
        self.assertEqual(cloud.calls[-1][1], "")
        with self.assertRaises(ValueError):
            provider.select("unknown")

    def test_settings_default_local_and_require_isolated_agent(self) -> None:
        settings = create_settings(environ={})
        self.assertEqual(settings.providers.chat_provider, "local")
        self.assertEqual(settings.providers.openai_chat_agent, "fusion-dialogue")
        with self.assertRaises(ConfigurationError):
            create_settings(environ={"FUSION_READER_OPENAI_CHAT_AGENT": "main"})
        with self.assertRaises(ConfigurationError):
            create_settings(environ={"FUSION_READER_CHAT_PROVIDER": "unknown"})

    def test_facade_switches_dialogue_provider_and_media_stays_local(self) -> None:
        local = NullChatProvider("local")
        cloud = NullChatProvider("cloud")
        provider = SelectableChatProvider({"local": local, "openai": cloud})
        app = test_app()
        app.conversation = ConversationCore(provider)
        app._dialogue_service.conversation = app.conversation
        changed = app.set_chat_provider("openai")
        self.assertTrue(changed["ok"])
        self.assertEqual(changed["id"], "openai")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = create_settings(
                repository_root=Path.cwd(),
                environ={
                    "HOME": str(root),
                    "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                    "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                    "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
                },
            )
            context = WebContext(app=app, settings=settings, runtime_info={})
            self.assertIs(context.media.chat, local)

    def test_selected_provider_persists_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_provider = SelectableChatProvider(
                {"local": NullChatProvider("local"), "openai": NullChatProvider("cloud")}
            )
            first = test_app(
                root=root,
                conversation=ConversationCore(first_provider),
                register_cleanup=False,
            )
            try:
                self.assertTrue(first.set_chat_provider("openai")["ok"])
            finally:
                close_test_app(first)

            second_provider = SelectableChatProvider(
                {"local": NullChatProvider("local"), "openai": NullChatProvider("cloud")}
            )
            second = test_app(
                root=root,
                conversation=ConversationCore(second_provider),
                register_cleanup=False,
            )
            try:
                self.assertEqual(second.chat_provider_status()["id"], "openai")
            finally:
                close_test_app(second)

    def test_setup_restricts_only_fusion_dialogue_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "openclaw.json"
            original = {
                "gateway": {"port": 1234},
                "agents": {
                    "list": [
                        {"id": "main", "model": "local/model", "tools": {"profile": "full"}},
                        {"id": "fusion-research", "tools": {"allow": ["web_search"]}},
                        {"id": "fusion-dialogue", "model": "openai/gpt-5.6-sol"},
                    ]
                },
            }
            config.write_text(json.dumps(original), encoding="utf-8")

            backup = setup_fusion_openai_dialogue.restrict_agent(config)
            updated = json.loads(config.read_text(encoding="utf-8"))

            self.assertTrue(backup.is_file())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
            self.assertEqual(updated["gateway"], original["gateway"])
            self.assertEqual(updated["agents"]["list"][0], original["agents"]["list"][0])
            self.assertEqual(updated["agents"]["list"][1], original["agents"]["list"][1])
            dialogue = updated["agents"]["list"][2]
            self.assertEqual(dialogue["tools"]["profile"], "minimal")
            self.assertIn("exec", dialogue["tools"]["deny"])


if __name__ == "__main__":
    unittest.main()
