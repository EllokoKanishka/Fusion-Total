from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from fusion_reader_v2.dialogue import FasterWhisperServerSTTProvider
from fusion_reader_v2.services.media import _MediaSignal


class Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class StreamingConnection:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.path = ""
        self.chunks: list[int] = []
        self.sock = None

    def putrequest(self, _method: str, path: str) -> None:
        self.path = path

    def putheader(self, _name: str, _value: str) -> None:
        return None

    def endheaders(self) -> None:
        return None

    def send(self, chunk: bytes) -> None:
        self.chunks.append(len(chunk))

    def getresponse(self):
        response = Response(self.payload)
        response.status = 200
        return response

    def close(self) -> None:
        return None


class MediaSTTMetadataTests(unittest.TestCase):
    def test_deadline_and_cancel_interrupt_headers_and_response_body(self) -> None:
        for phase in ("headers", "body"):
            for reason in ("timeout", "cancelled"):
                with self.subTest(phase=phase, reason=reason), tempfile.TemporaryDirectory() as tmp:
                    arrived = threading.Event()
                    release = threading.Event()
                    cancelled = threading.Event()

                    class Handler(BaseHTTPRequestHandler):
                        def log_message(self, *_args):
                            pass

                        def do_POST(self):
                            self.rfile.read(int(self.headers.get("Content-Length", "0")))
                            if self.path.startswith("/cancel/"):
                                cancelled.set()
                                self.send_response(200)
                                self.end_headers()
                                self.wfile.write(b'{"ok": true}')
                                return
                            if phase == "body":
                                self.send_response(200)
                                self.send_header("Content-Length", "100")
                                self.end_headers()
                                self.wfile.write(b"{")
                                self.wfile.flush()
                            arrived.set()
                            release.wait(5)

                    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
                    server_thread.start()
                    source = Path(tmp) / "conference.flac"
                    source.write_bytes(b"audio")
                    event = threading.Event()
                    signal = _MediaSignal(event, time.monotonic() + 5)
                    provider = FasterWhisperServerSTTProvider(
                        base_url=f"http://127.0.0.1:{server.server_port}", timeout_seconds=5
                    )
                    result = []
                    worker = threading.Thread(
                        target=lambda: result.append(
                            provider.transcribe_file_cancellable(
                                source, cancel_event=signal, request_id="deadline-test", long_form=True
                            )
                        )
                    )
                    # The pipeline has spent almost all its budget normalizing.
                    if reason == "timeout":
                        signal.deadline = time.monotonic() + 0.3
                    try:
                        worker.start()
                        self.assertTrue(arrived.wait(2))
                        if reason == "cancelled":
                            event.set()
                        worker.join(2)
                        self.assertFalse(worker.is_alive(), "STT exceeded the remaining pipeline budget")
                        self.assertFalse(result[0].ok)
                        self.assertEqual(result[0].detail, reason)
                        self.assertTrue(cancelled.is_set(), "server inference must also be cancelled")
                        self.assertEqual(provider._connections, {})
                        self.assertFalse(any(t.name == "fusion-stt-deadline" for t in threading.enumerate()))
                    finally:
                        event.set()
                        release.set()
                        worker.join(6)
                        server.shutdown()
                        server.server_close()
                        server_thread.join(2)

    def test_expired_deadline_does_not_open_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "conference.flac"
            source.write_bytes(b"audio")
            signal = _MediaSignal(threading.Event(), time.monotonic() - 1)
            with mock.patch("http.client.HTTPConnection") as connection:
                result = FasterWhisperServerSTTProvider().transcribe_file_cancellable(source, cancel_event=signal)
            self.assertEqual(result.detail, "timeout")
            connection.assert_not_called()

    def test_server_result_preserves_detected_language_and_timed_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "conference.flac"
            source.write_bytes(b"synthetic")
            provider = FasterWhisperServerSTTProvider(timeout_seconds=99)
            payload = {
                "ok": True,
                "text": "First idea. Second idea.",
                "provider": "faster_whisper_server",
                "detected_language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.1, "text": "First idea."},
                    {"start": 2.1, "end": 4.2, "text": "Second idea."},
                ],
            }
            with mock.patch("urllib.request.urlopen", return_value=Response(payload)) as opened:
                result = provider.transcribe_file(source, mime="audio/flac", language="auto")
            self.assertTrue(result.ok)
            self.assertEqual(result.detected_language, "en")
            self.assertEqual(len(result.segments), 2)
            self.assertEqual(result.segments[1].start, 2.1)
            request = opened.call_args.args[0]
            self.assertIn("language=auto", request.full_url)

    def test_long_form_client_streams_file_in_bounded_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "conference.flac"
            source.write_bytes(b"x" * (2 * 1024 * 1024 + 17))
            provider = FasterWhisperServerSTTProvider(base_url="http://127.0.0.1:8021", timeout_seconds=99)
            connection = StreamingConnection({"ok": True, "text": "Texto", "detected_language": "es"})
            with mock.patch("http.client.HTTPConnection", return_value=connection):
                result = provider.transcribe_file_cancellable(
                    source,
                    mime="audio/flac",
                    language="auto",
                    request_id="job-1",
                    long_form=True,
                )
            self.assertTrue(result.ok)
            self.assertEqual(connection.chunks, [1024 * 1024, 1024 * 1024, 17])
            self.assertIn("request_id=job-1", connection.path)
            self.assertIn("long_form=1", connection.path)


if __name__ == "__main__":
    unittest.main()
