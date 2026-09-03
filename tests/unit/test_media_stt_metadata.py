from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2.dialogue import FasterWhisperServerSTTProvider


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
