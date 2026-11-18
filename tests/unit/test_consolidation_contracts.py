from pathlib import Path
from unittest.mock import patch
import importlib.util
import struct
import unittest

from scripts.benchmark_reasoning_models import TASKS, score_answer


class ConsolidationContractsTests(unittest.TestCase):
    def test_arithmetic_and_logic_oracles_reject_old_wrong_answers(self):
        tasks = {task.key: task for task in TASKS}
        self.assertTrue(score_answer(tasks["arithmetic_constraint"], "12")[0])
        self.assertFalse(score_answer(tasks["arithmetic_constraint"], "14")[0])
        self.assertFalse(score_answer(tasks["arithmetic_constraint"], "112")[0])
        self.assertTrue(score_answer(tasks["syllogism"], "sí se deduce")[0])
        self.assertFalse(score_answer(tasks["syllogism"], "no se deduce")[0])

    def test_legacy_pcm_fallback_resamples_streaming_frames(self):
        path = Path(__file__).resolve().parents[2] / "scripts/molbot_direct_chat/stt_local.py"
        spec = importlib.util.spec_from_file_location("resampling_fixture", path)
        module = importlib.util.module_from_spec(spec)
        import sys

        sys.modules[spec.name] = module
        with patch.dict(sys.modules, {"audioop": None}):
            spec.loader.exec_module(module)
        samples = struct.pack("<" + "h" * 480, *range(480))
        output, state = module.audioop.ratecv(samples, 2, 1, 48000, 16000, None)
        self.assertEqual(len(output), 160 * 2)
        self.assertNotEqual(output, samples)
        second, _ = module.audioop.ratecv(samples, 2, 1, 48000, 16000, state)
        self.assertEqual(len(second), 160 * 2)
