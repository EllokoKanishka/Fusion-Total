from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_qwen3_asr import (
    count_phrase,
    entity_counts,
    normalize_qwen_audio,
    normalized_tokens,
    serialize_timestamps,
)


class Qwen3ASRBenchmarkHelpersTests(unittest.TestCase):
    def test_normalized_tokens_are_case_and_accent_insensitive(self) -> None:
        self.assertEqual(normalized_tokens("Trántor, PSICOHISTORIA."), ["trantor", "psicohistoria"])

    def test_count_phrase_uses_token_boundaries(self) -> None:
        tokens = normalized_tokens("Hari Seldon habló con Hari Seldon. Seldoniano no cuenta.")
        self.assertEqual(count_phrase(tokens, "Hari Seldon"), 2)
        self.assertEqual(count_phrase(tokens, "Seldon"), 2)

    def test_entity_counts_support_multiword_names(self) -> None:
        counts = entity_counts(
            "Isaac Asimov presenta a Gaal Dornick en Trántor.",
            ["Isaac Asimov", "Gaal Dornick", "Trantor", "Terminus"],
        )
        self.assertEqual(counts["Isaac Asimov"], 1)
        self.assertEqual(counts["Gaal Dornick"], 1)
        self.assertEqual(counts["Trantor"], 1)
        self.assertEqual(counts["Terminus"], 0)

    def test_serialize_timestamps_accepts_objects_and_dicts(self) -> None:
        @dataclass
        class Stamp:
            text: str
            start_time: float
            end_time: float

        rows = serialize_timestamps(
            [
                Stamp("Hola", 0.1, 0.4),
                {"text": "mundo", "start_time": 0.4, "end_time": 0.9},
            ]
        )
        self.assertEqual(rows[0], {"text": "Hola", "start": 0.1, "end": 0.4})
        self.assertEqual(rows[1], {"text": "mundo", "start": 0.4, "end": 0.9})

    def test_normalize_qwen_audio_uses_stable_ffmpeg_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "clip.mp4"
            target = Path(temp_dir) / "clip.flac"
            source.write_bytes(b"container")

            def fake_run(command, **kwargs):
                self.assertEqual(command[0], "/usr/bin/ffmpeg")
                self.assertIn("-vn", command)
                self.assertEqual(command[command.index("-ar") + 1], "16000")
                self.assertEqual(command[command.index("-ac") + 1], "1")
                self.assertEqual(command[command.index("-c:a") + 1], "flac")
                self.assertEqual(command[-1], str(target))
                self.assertFalse(kwargs["check"])
                target.write_bytes(b"FLAC")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                patch("scripts.benchmark_qwen3_asr.shutil.which", return_value="/usr/bin/ffmpeg"),
                patch("scripts.benchmark_qwen3_asr.subprocess.run", side_effect=fake_run),
            ):
                elapsed = normalize_qwen_audio(source, target)

        self.assertGreaterEqual(elapsed, 0.0)

    def test_normalize_qwen_audio_requires_ffmpeg(self) -> None:
        with patch("scripts.benchmark_qwen3_asr.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg_not_available"):
                normalize_qwen_audio(Path("input.mp4"), Path("output.flac"))


if __name__ == "__main__":
    unittest.main()
