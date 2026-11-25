from __future__ import annotations

import io
import json
import sqlite3
import socket
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.request import Request, urlopen
from unittest.mock import patch
from uuid import uuid4

from fusion_reader_v2.conversation import ConversationCore, OllamaChatProvider, SelectableChatProvider
from fusion_reader_v2.laboratory_turn import (
    ClientDisconnect, LaboratoryTurnCancelled, laboratory_cancellation, read_turn_response,
)
from fusion_reader_v2.services.laboratory_archive import ArchiveScope, LaboratoryArchive
from fusion_reader_v2.turn_provider import LABORATORY_PROVIDERS, use_turn_provider
from tests.helpers import managed_test_app


class LaboratoryRegressionTests(unittest.TestCase):
    def test_idle_client_with_request_timeout_stays_connected(self):
        downstream, client = socket.socketpair()
        with downstream, client:
            downstream.settimeout(0.15)
            signal = ClientDisconnect(downstream)
            self.assertFalse(signal.is_set())
            client.close()
            self.assertTrue(signal.is_set())

    def test_disconnect_interrupts_a_blocked_local_provider_read(self):
        waiting = Event()
        release = Event()

        class SlowProvider(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                waiting.set()
                release.wait(5)

        with ThreadingHTTPServer(("127.0.0.1", 0), SlowProvider) as upstream:
            serve = Thread(target=upstream.serve_forever, daemon=True)
            serve.start()
            downstream, client = socket.socketpair()
            downstream.settimeout(0.15)
            request = Request(f"http://127.0.0.1:{upstream.server_port}/api/chat", b"{}")

            def ask():
                with laboratory_cancellation(downstream):
                    return read_turn_response(request, timeout=10, urlopen=urlopen)

            with downstream, client, ThreadPoolExecutor(max_workers=1) as executor:
                try:
                    response = executor.submit(ask)
                    self.assertTrue(waiting.wait(3))
                    client.close()
                    with self.assertRaises(LaboratoryTurnCancelled):
                        response.result(timeout=2)
                finally:
                    release.set()
                    upstream.shutdown()
                    serve.join(3)

    def test_selected_gemma_pins_model_bounds_input_and_preserves_truncation(self):
        provider = OllamaChatProvider(default_model="gemma:fixture", context_length=8192)
        core = ConversationCore(SelectableChatProvider({"local": provider, "gemma": provider}))
        calls = []

        def send(request, **_):
            calls.append(json.loads(request.data))
            return io.BytesIO(b'{"done_reason":"length","message":{"content":"Una parte"}}')

        message = "Pregunta actual"
        with patch("urllib.request.urlopen", side_effect=send), use_turn_provider(
            "gemma", LABORATORY_PROVIDERS, "local"
        ):
            result = core._chat(
                [{"role": "system", "content": "S" * 30_000}, {"role": "user", "content": message}],
                model="qwen3:14b-q8_0",
                num_predict=2048,
            )
        payload = calls[0]
        self.assertEqual(payload["model"], "gemma:fixture")
        self.assertEqual(payload["messages"][-1]["content"], message)
        self.assertLessEqual(sum(len(x["content"]) for x in payload["messages"]), 11_776)
        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)

    def test_catalog_reachable_but_model_not_ready(self):
        provider = OllamaChatProvider(default_model="gemma:fixture")
        with tempfile.TemporaryDirectory() as directory, managed_test_app(root=Path(directory)) as app:
            app.conversation.provider = SelectableChatProvider({"local": provider})
            with patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(b'{"models": []}')):
                state = app.chat_provider_status()
        self.assertTrue(state["connection_verified"])
        self.assertFalse(state["ready"])
        self.assertEqual(state["detail"], "model_not_installed")

    def test_archive_migrates_old_rows_and_pages_without_repeating_new_inserts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            session = str(uuid4())
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(
                    """CREATE TABLE turns (
                        id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL,
                        question TEXT NOT NULL, answer TEXT NOT NULL, sources TEXT NOT NULL);"""
                )
                connection.execute("INSERT INTO turns VALUES (1, ?, 'antes', 'respuesta', '[]')", (session,))
            scope = ArchiveScope(session, "local", "", "free")
            archive = LaboratoryArchive(path)
            for number in range(35):
                archive.record(scope, str(number), "respuesta", [], None, None, truncated=number == 34)
            first = archive.turn_page(session)
            self.assertEqual(len(first["turns"]), 30)
            self.assertEqual(first["turns"][0]["question"], "5")
            self.assertTrue(first["turns"][-1]["truncated"])
            archive.record(scope, "nuevo", "respuesta", [], None, None)
            older = archive.turn_page(session, first["next_before"])
            self.assertEqual([x["question"] for x in older["turns"]], ["antes", "0", "1", "2", "3", "4"])
            self.assertIsNone(older["next_before"])
