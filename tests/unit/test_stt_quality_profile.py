from __future__ import annotations

import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "scripts" / "fusion_reader_v2_stt_server.py"
LAUNCHER_PATH = REPO_ROOT / "scripts" / "start_fusion_reader_v2_stt.sh"


class FakeWhisperModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(
        self,
        audio,
        *,
        language=None,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=True,
        initial_prompt=None,
        hotwords=None,
    ):
        self.calls.append(
            {
                "audio": audio,
                "language": language,
                "beam_size": beam_size,
                "vad_filter": vad_filter,
                "condition_on_previous_text": condition_on_previous_text,
                "initial_prompt": initial_prompt,
                "hotwords": hotwords,
            }
        )
        segments = [SimpleNamespace(text="Hari Seldon llegó a Trantor.", start=0.0, end=2.0)]
        return segments, SimpleNamespace(language="es")


class STTQualityProfileTests(unittest.TestCase):
    def _load_server(self, env: dict[str, str] | None = None):
        model = FakeWhisperModel()
        factory = mock.Mock(return_value=model)
        fake_whisper = SimpleNamespace(WhisperModel=factory)
        with (
            mock.patch.dict(os.environ, env or {}, clear=True),
            mock.patch.dict(sys.modules, {"faster_whisper": fake_whisper}),
        ):
            server = runpy.run_path(str(SERVER_PATH))
        return server, model, factory

    def test_server_defaults_to_quality_first_gpu_profile(self) -> None:
        server, _model, factory = self._load_server()
        self.assertEqual(server["MODEL_NAME"], "large-v3-turbo")
        self.assertEqual(server["DEVICE"], "cuda")
        self.assertEqual(server["COMPUTE_TYPE"], "float16")
        self.assertEqual(server["BEAM_SIZE"], 5)
        factory.assert_called_once_with("large-v3-turbo", device="cuda", compute_type="float16")

    def test_hotwords_and_prompt_reach_faster_whisper_without_rewriting_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hotwords_file = Path(tmp) / "hotwords.txt"
            hotwords_file.write_text(
                "# Domain vocabulary\nHari Seldon\nTrantor\nTerminus\ntrantor\n",
                encoding="utf-8",
            )
            server, model, _factory = self._load_server(
                {
                    "FUSION_READER_STT_HOTWORDS": "Isaac Asimov, psicohistoria",
                    "FUSION_READER_STT_HOTWORDS_FILE": str(hotwords_file),
                    "FUSION_READER_STT_INITIAL_PROMPT": "Audiolibro de Fundación en castellano.",
                }
            )

        text, _duration_ms, metadata = server["transcribe_wav"](
            Path("/tmp/fusion-quality-test.flac"),
            "es",
            long_form=True,
        )
        self.assertEqual(text, "Hari Seldon llegó a Trantor.")
        self.assertEqual(metadata["detected_language"], "es")
        self.assertEqual(len(model.calls), 1)
        call = model.calls[0]
        self.assertEqual(call["beam_size"], 5)
        self.assertTrue(call["vad_filter"])
        self.assertTrue(call["condition_on_previous_text"])
        self.assertEqual(call["initial_prompt"], "Audiolibro de Fundación en castellano.")
        self.assertEqual(
            call["hotwords"],
            "Isaac Asimov, psicohistoria, Hari Seldon, Trantor, Terminus",
        )

    def test_per_request_context_is_merged_without_mutating_global_context(self) -> None:
        server, model, _factory = self._load_server(
            {
                "FUSION_READER_STT_HOTWORDS": "Isaac Asimov, Trantor",
                "FUSION_READER_STT_INITIAL_PROMPT": "Audiolibro en castellano.",
            }
        )
        text, _duration_ms, _metadata = server["transcribe_wav"](
            Path("/tmp/fusion-context-test.flac"),
            "es",
            long_form=True,
            initial_prompt="Obra Fundación de Isaac Asimov.",
            hotwords="Hari Seldon, trantor, Gaal Dornick, Terminus",
        )
        self.assertEqual(text, "Hari Seldon llegó a Trantor.")
        call = model.calls[0]
        self.assertEqual(
            call["initial_prompt"],
            "Audiolibro en castellano. Obra Fundación de Isaac Asimov.",
        )
        self.assertEqual(
            call["hotwords"],
            "Isaac Asimov, Trantor, Hari Seldon, Gaal Dornick, Terminus",
        )
        self.assertEqual(server["INITIAL_PROMPT"], "Audiolibro en castellano.")
        self.assertEqual(server["HOTWORDS"], "Isaac Asimov, Trantor")

    def test_hotword_limits_are_bounded_and_case_insensitive(self) -> None:
        server, _model, _factory = self._load_server(
            {
                "FUSION_READER_STT_HOTWORDS": "Trantor,trantor,Terminus,Hari Seldon",
                "FUSION_READER_STT_HOTWORD_MAX_ITEMS": "2",
                "FUSION_READER_STT_CONTEXT_MAX_CHARS": "32",
            }
        )
        self.assertEqual(server["HOTWORDS"], "Trantor, Terminus")
        self.assertLessEqual(len(server["HOTWORDS"]), 32)
        merged = server["_context_options"]("context", "TERMINUS, Gaal Dornick, Anacreon")
        self.assertEqual(merged["hotwords"], "Trantor, Terminus")

    def test_launcher_selects_heavy_model_only_for_cuda_by_default(self) -> None:
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn("STT_MODEL_WAS_SET", launcher)
        self.assertIn("export FUSION_READER_STT_MODEL=large-v3-turbo", launcher)
        self.assertIn("export FUSION_READER_STT_MODEL=small", launcher)
        self.assertIn("export FUSION_READER_STT_BEAM_SIZE=5", launcher)
        self.assertIn("export FUSION_READER_STT_BEAM_SIZE=2", launcher)


if __name__ == "__main__":
    unittest.main()
