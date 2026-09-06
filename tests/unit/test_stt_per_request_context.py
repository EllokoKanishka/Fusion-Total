from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import dialogue
from fusion_reader_v2.services.media import (
    MEDIA_STT_HOTWORDS_MAX_CHARS,
    MEDIA_STT_PROMPT_MAX_CHARS,
    _bounded_job_context,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class STTPerRequestContextTests(unittest.TestCase):
    def test_server_provider_encodes_context_only_for_that_request(self) -> None:
        provider = dialogue.FasterWhisperServerSTTProvider(base_url="http://local", timeout_seconds=1)
        seen_urls: list[str] = []

        def respond(request, **_kwargs):
            seen_urls.append(request.full_url)
            return _Response({"ok": True, "text": "texto", "provider": "server"})

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "audio.wav"
            source.write_bytes(b"RIFF")
            with mock.patch.object(dialogue.urllib.request, "urlopen", side_effect=respond):
                self.assertTrue(
                    provider.transcribe_file(
                        source,
                        mime="audio/wav",
                        language="es",
                        initial_prompt="Fundación de Isaac Asimov",
                        hotwords="Hari Seldon, Gaal Dornick, Trantor",
                    ).ok
                )
                self.assertTrue(provider.transcribe_file(source, mime="audio/wav", language="es").ok)

        first = urllib.parse.parse_qs(urllib.parse.urlsplit(seen_urls[0]).query)
        second = urllib.parse.parse_qs(urllib.parse.urlsplit(seen_urls[1]).query)
        self.assertEqual(first["initial_prompt"], ["Fundación de Isaac Asimov"])
        self.assertEqual(first["hotwords"], ["Hari Seldon, Gaal Dornick, Trantor"])
        self.assertNotIn("initial_prompt", second)
        self.assertNotIn("hotwords", second)

    def test_auto_provider_forwards_context_to_healthy_primary(self) -> None:
        primary = mock.Mock(spec=dialogue.STTProvider)
        fallback = mock.Mock(spec=dialogue.STTProvider)
        primary.name = "primary"
        fallback.name = "fallback"
        primary.health.return_value = {"ok": True}
        fallback.health.return_value = {"ok": False}
        primary.transcribe_file.return_value = dialogue.TranscriptResult(True, text="ok")

        auto = dialogue.AutoSTTProvider(primary, fallback)
        result = auto.transcribe_file(
            "audio.wav",
            initial_prompt="Fundación",
            hotwords="Hari Seldon, Trantor",
        )

        self.assertTrue(result.ok)
        primary.transcribe_file.assert_called_once_with(
            "audio.wav",
            mime="",
            language="es",
            initial_prompt="Fundación",
            hotwords="Hari Seldon, Trantor",
        )
        fallback.transcribe_file.assert_not_called()

    def test_media_job_context_is_bounded_before_background_work(self) -> None:
        self.assertEqual(_bounded_job_context("  Hari\nSeldon  ", 20), "Hari Seldon")
        self.assertEqual(len(_bounded_job_context("x" * 5000, MEDIA_STT_PROMPT_MAX_CHARS)), 1200)
        self.assertEqual(len(_bounded_job_context("y" * 5000, MEDIA_STT_HOTWORDS_MAX_CHARS)), 2400)


if __name__ == "__main__":
    unittest.main()
