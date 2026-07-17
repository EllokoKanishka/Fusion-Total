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


if __name__ == "__main__":
    unittest.main()
