from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import dialogue


class Response:
    def __init__(self, payload: object = None, raw: bytes | None = None) -> None:
        self.raw = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class STTProviderMatrixTests(unittest.TestCase):
    def test_base_null_filter_json_and_normalization_contracts(self) -> None:
        self.assertFalse(dialogue.STTProvider().health()["ok"])
        self.assertFalse(dialogue.STTProvider().transcribe_file("missing").ok)
        with tempfile.NamedTemporaryFile() as handle:
            provider = dialogue.NullSTTProvider("texto")
            self.assertTrue(provider.transcribe_file(handle.name).ok)
            self.assertEqual(len(provider.calls), 1)
        self.assertFalse(dialogue.is_hallucinated_transcript(""))
        self.assertTrue(dialogue.is_hallucinated_transcript("Gracias por ver el video"))
        self.assertFalse(dialogue.is_hallucinated_transcript("Una frase normal para el lector"))
        self.assertEqual(dialogue.normalize_stt_provider("faster-whisper"), "server")
        self.assertEqual(dialogue.normalize_stt_provider("cli"), "cli")
        self.assertEqual(dialogue.normalize_stt_provider("unknown"), "auto")
        self.assertEqual(dialogue._json_response(b'{"ok":true}'), {"ok": True})
        self.assertEqual(dialogue._json_response(b"bad", {"ok": True}), {"ok": True})

    def test_whisper_cli_health_and_transcription_matrix(self) -> None:
        provider = dialogue.WhisperCliSTTProvider(command="whisper", timeout_seconds=1)
        with mock.patch.object(dialogue.shutil, "which", return_value=None):
            self.assertFalse(provider.health()["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "audio.wav"
            self.assertEqual(provider.transcribe_file(source).detail, "empty_audio")
            source.write_bytes(b"RIFF")
            with mock.patch.object(dialogue.shutil, "which", return_value=None):
                self.assertEqual(provider.transcribe_file(source).detail, "command_not_found")

            with (
                mock.patch.object(dialogue.shutil, "which", return_value="/bin/whisper"),
                mock.patch.object(dialogue.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)),
            ):
                self.assertEqual(provider.transcribe_file(source).detail, "timeout")
            failed = subprocess.CompletedProcess([], 2, stdout="", stderr="failure\nlast")
            with (
                mock.patch.object(dialogue.shutil, "which", return_value="/bin/whisper"),
                mock.patch.object(dialogue.subprocess, "run", return_value=failed),
            ):
                self.assertEqual(provider.transcribe_file(source).detail, "last")

            def completed(text: str):
                def run(cmd, **_kwargs):
                    out_dir = Path(cmd[cmd.index("--output_dir") + 1])
                    (out_dir / f"{source.stem}.txt").write_text(text, encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

                return run

            for text, ok, detail in (
                ("Texto normal\n de prueba", True, ""),
                ("", False, "empty_transcript"),
                ("Suscríbete al canal", False, "hallucinated_transcript"),
            ):
                with (
                    self.subTest(text=text),
                    mock.patch.object(dialogue.shutil, "which", return_value="/bin/whisper"),
                    mock.patch.object(dialogue.subprocess, "run", side_effect=completed(text)),
                ):
                    result = provider.transcribe_file(source)
                    self.assertEqual(result.ok, ok)
                    self.assertEqual(result.detail, detail)

            out = Path(tmp) / "out"
            out.mkdir()
            self.assertEqual(provider._read_transcript(out, source), "")
            (out / "other.txt").write_text("otro", encoding="utf-8")
            self.assertEqual(provider._read_transcript(out, source), "otro")
            self.assertEqual(provider._clean_text(" a\r\n b "), "a b")

    def test_faster_whisper_health_and_transcription_matrix(self) -> None:
        provider = dialogue.FasterWhisperServerSTTProvider(base_url="http://local", timeout_seconds=1)
        with mock.patch.object(dialogue.urllib.request, "urlopen", return_value=Response({"ok": True})):
            self.assertTrue(provider.health()["ok"])
        with mock.patch.object(dialogue.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertFalse(provider.health()["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "audio.wav"
            self.assertEqual(provider.transcribe_file(source).detail, "empty_audio")
            source.write_bytes(b"RIFF")
            error = urllib.error.HTTPError("http://local", 500, "bad", {}, None)
            with mock.patch.object(dialogue.urllib.request, "urlopen", side_effect=error):
                self.assertEqual(provider.transcribe_file(source).detail, "http_500")
            with mock.patch.object(dialogue.urllib.request, "urlopen", side_effect=OSError("down")):
                self.assertIn("down", provider.transcribe_file(source).detail)
            cases = (
                ({"ok": False, "text": "x", "error": "failed", "convert_ms": 1}, False, "failed"),
                ({"ok": True, "text": "", "provider": "server"}, False, "empty_transcript"),
                ({"ok": True, "text": "Giraff", "provider": "server"}, False, "hallucinated_transcript"),
                ({"ok": True, "text": "Texto válido", "provider": "server", "duration_ms": 4}, True, ""),
            )
            for payload, ok, detail in cases:
                with (
                    self.subTest(payload=payload),
                    mock.patch.object(dialogue.urllib.request, "urlopen", return_value=Response(payload)),
                ):
                    result = provider.transcribe_file(source, mime="audio/wav", language="")
                    self.assertEqual(result.ok, ok)
                    self.assertEqual(result.detail, detail)

    def test_auto_health_transcription_defaults_and_command_resolution(self) -> None:
        primary = mock.Mock(spec=dialogue.STTProvider)
        fallback = mock.Mock(spec=dialogue.STTProvider)
        primary.name = "primary"
        fallback.name = "fallback"
        primary.health.return_value = {"ok": True}
        fallback.health.return_value = {"ok": True}
        primary.transcribe_file.return_value = dialogue.TranscriptResult(True, text="primary")
        auto = dialogue.AutoSTTProvider(primary, fallback)
        self.assertEqual(auto.health()["selected"], "primary")
        self.assertEqual(auto.transcribe_file("audio").text, "primary")
        primary.transcribe_file.return_value = dialogue.TranscriptResult(False, detail="hallucinated_transcript")
        self.assertEqual(auto.transcribe_file("audio").detail, "hallucinated_transcript")
        primary.health.return_value = {"ok": False}
        fallback.transcribe_file.return_value = dialogue.TranscriptResult(True, text="fallback")
        self.assertEqual(auto.health()["selected"], "fallback")
        self.assertEqual(auto.transcribe_file("audio").text, "fallback")

        for selected, expected in (
            ("cli", dialogue.WhisperCliSTTProvider),
            ("server", dialogue.FasterWhisperServerSTTProvider),
            ("auto", dialogue.AutoSTTProvider),
        ):
            with mock.patch.dict("os.environ", {"FUSION_READER_STT_PROVIDER": selected}):
                self.assertIsInstance(dialogue.default_stt_provider(), expected)
        with mock.patch.object(dialogue.shutil, "which", return_value="/bin/whisper"):
            self.assertEqual(dialogue._default_whisper_command(), "/bin/whisper")
        with (
            mock.patch.object(dialogue.shutil, "which", return_value=None),
            mock.patch.object(Path, "exists", return_value=False),
        ):
            self.assertEqual(dialogue._default_whisper_command(), "whisper")


if __name__ == "__main__":
    unittest.main()
