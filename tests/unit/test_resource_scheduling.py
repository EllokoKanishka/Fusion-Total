import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.unit import test_stt_quality_profile


class ResourceSchedulingTests(unittest.TestCase):
    def test_dialogue_runs_between_long_audio_segments_without_losing_any(self):
        server, model, _ = test_stt_quality_profile.STTQualityProfileTests()._load_server()
        started, waiting = threading.Event(), threading.Event()
        order, results, errors = [], {}, []

        def transcribe(audio, **kwargs):
            if audio == "long.wav":

                def segments():
                    for index in range(3):
                        order.append(f"long-{index}")
                        if index == 0:
                            started.set()
                            waiting.wait(2)
                            time.sleep(0.04)
                        yield SimpleNamespace(text=f"Parte {index}.", start=index, end=index + 1)

                return segments(), SimpleNamespace(language="es")
            order.append("dialogue")
            return [SimpleNamespace(text="Consulta breve.", start=0, end=1)], SimpleNamespace(language="es")

        model.transcribe = transcribe

        def run(name, long_form):
            try:
                results[name] = server["transcribe_wav"](Path(name + ".wav"), "es", long_form=long_form)
            except Exception as exc:
                errors.append(exc)

        long = threading.Thread(target=run, args=("long", True))
        short = threading.Thread(target=run, args=("short", False))
        long.start()
        try:
            self.assertTrue(started.wait(2))
            short.start()
            waiting.set()
            long.join(3)
            short.join(3)
            self.assertFalse(long.is_alive() or short.is_alive())
            self.assertEqual(errors, [])
            self.assertLess(order.index("dialogue"), order.index("long-1"))
            self.assertEqual(results["long"][0], "Parte 0. Parte 1. Parte 2.")
            self.assertEqual(len(results["long"][2]["segments"]), 3)
        finally:
            waiting.set()
            long.join(3)
            if short.ident is not None:
                short.join(3)

    def test_expired_request_never_enters_native_inference(self):
        server, model, _ = test_stt_quality_profile.STTQualityProfileTests()._load_server()
        cancel = threading.Event()
        cancel.deadline = time.monotonic() - 1
        with self.assertRaisesRegex((TimeoutError, RuntimeError), "deadline"):
            server["transcribe_wav"](Path("short.wav"), "es", cancel_event=cancel)
        self.assertEqual(model.calls, [])

    def _http_server(self):
        from http.server import ThreadingHTTPServer

        module, model, _ = test_stt_quality_profile.STTQualityProfileTests()._load_server()
        server = ThreadingHTTPServer(("127.0.0.1", 0), module["Handler"])
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        return module, model, server

    def test_http_queue_deadline_is_explicit_and_cleans_request_registry(self):
        import http.client
        import json

        module, model, server = self._http_server()
        gate = module["transcribe_wav"].__globals__["INFERENCE_LOCK"]
        replies, errors = [], []

        def request():
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            try:
                connection.request(
                    "POST",
                    "/transcribe?request_id=deadline-case&timeout_seconds=0.03&mime=audio/flac",
                    body=b"fixture",
                    headers={"Content-Type": "audio/flac"},
                )
                response = connection.getresponse()
                replies.append((response.status, json.loads(response.read())))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        with gate.slot():
            client = threading.Thread(target=request)
            client.start()
            client.join(1)
        self.assertFalse(client.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(replies[0][0], 504)
        self.assertEqual(replies[0][1]["error"], "deadline_exceeded")
        self.assertEqual(model.calls, [])
        self.assertEqual(module["ACTIVE_REQUESTS"], {})

    def test_http_incomplete_upload_cannot_outlive_its_deadline(self):
        import http.client
        import json

        module, model, server = self._http_server()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            connection.putrequest("POST", "/transcribe?request_id=upload-case&timeout_seconds=0.03&mime=audio/flac")
            connection.putheader("Content-Length", "100")
            connection.putheader("Content-Type", "audio/flac")
            connection.endheaders()
            connection.send(b"x")
            response = connection.getresponse()
            self.assertEqual(response.status, 504)
            self.assertEqual(json.loads(response.read())["error"], "deadline_exceeded")
        finally:
            connection.close()
        self.assertEqual(model.calls, [])
        self.assertEqual(module["ACTIVE_REQUESTS"], {})
