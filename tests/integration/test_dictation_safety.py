from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from fusion_reader_v2.transcript_correction import CorrectionOutcome
from tests.integration import test_web_server as support


class DictationHTTPTests(unittest.TestCase):
    _settings = support.WebServerIntegrationTests._settings
    _start = support.WebServerIntegrationTests._start
    _request = support.WebServerIntegrationTests._request

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.server = self._start(Path(tmp.name))
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_proofreader_is_reachable_and_returns_corrected_selection(self):
        class Corrector:
            def health(self):
                return {"ok": True, "model": "synthetic"}

            def correct(self, text, **_kwargs):
                return CorrectionOutcome("Hola, mundo.", True, True, "accepted", 1, "synthetic")

        self.server.context.app._dictation_corrector = Corrector()
        code, result = self._request(self.base, "/api/dictation/proofread", {"text": "hola mundo"})
        self.assertEqual(code, 200)
        self.assertEqual(result["text"], "Hola, mundo.")
        self.assertEqual(result["changed_parts"], 1)

    def test_transport_failure_after_a_changed_paragraph_returns_the_entire_original(self):
        class Corrector:
            def health(self):
                return {"ok": True, "model": "synthetic"}

            def correct(self, text, **_kwargs):
                if text == "hola mundo":
                    return CorrectionOutcome("Hola, mundo.", True, True, "accepted", 1, "synthetic")
                return CorrectionOutcome(text, False, False, "connection_refused", 1, "synthetic")

        self.server.context.app._dictation_corrector = Corrector()
        original = "hola mundo\n\nNo tocar este segundo párrafo."
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(self.base, "/api/dictation/proofread", {"text": original})
        data = json.loads(raised.exception.read())
        self.assertEqual(raised.exception.code, 502)
        self.assertEqual(data["text"], original)
        self.assertEqual(data["error"], "dictation_proofread_failed")

    def test_invalid_or_unbounded_proofreading_does_not_call_a_model(self):
        class Corrector:
            def health(self):
                raise AssertionError("validation must happen before contacting a provider")

        self.server.context.app._dictation_corrector = Corrector()
        for value, status in [("", 400), ("x" * 12_001, 413), ({"text": "invalid"}, 400)]:
            with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError) as raised:
                self._request(self.base, "/api/dictation/proofread", {"text": value})
            self.assertEqual(raised.exception.code, status)

    def test_unavailable_provider_has_an_explicit_http_error(self):
        class Corrector:
            def health(self):
                return {"ok": False, "detail": "synthetic_unavailable"}

        self.server.context.app._dictation_corrector = Corrector()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(self.base, "/api/dictation/proofread", {"text": "Conservar el original."})
        self.assertEqual(raised.exception.code, 503)
        self.assertEqual(json.loads(raised.exception.read())["error"], "dictation_proofread_unavailable")
