import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fusion_reader_v2.gpu_budget import GPUBudget, read_gpu_budget
from fusion_reader_v2.config import create_settings
from fusion_reader_v2.resources import ResourceController, dedicated_settings


class GPUBudgetTests(unittest.TestCase):
    def test_observed_occupancy_controls_admission_without_process_names(self):
        roomy = GPUBudget(32000, 4000, 4096)
        busy = GPUBudget(32000, 30000, 4096)
        self.assertTrue(roomy.permits(18000))
        self.assertFalse(busy.permits(0))
        self.assertFalse(busy.permits(18000))
        self.assertFalse(GPUBudget(None, None).permits(0))

    def test_telemetry_is_bounded_and_invalid_data_is_not_treated_as_free_memory(self):
        with patch(
            "fusion_reader_v2.gpu_budget.run_owned", return_value=SimpleNamespace(returncode=0, stdout="32000, 5000\n")
        ) as run:
            budget = read_gpu_budget(4096)
            self.assertEqual(budget.available_mib, 22904)
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
        with patch(
            "fusion_reader_v2.gpu_budget.run_owned", return_value=SimpleNamespace(returncode=0, stdout="unknown")
        ):
            self.assertIsNone(read_gpu_budget().available_mib)

    def test_dedicated_settings_do_not_modify_shared_endpoint_or_model_names(self):
        original = create_settings(repository_root=Path.cwd(), environ={})
        settings = dedicated_settings(original)
        self.assertEqual(settings.providers.ollama_url, "http://127.0.0.1:11435")
        self.assertEqual(original.providers.ollama_url, "http://127.0.0.1:11434")
        self.assertEqual(settings.providers.chat_model, original.providers.chat_model)
        self.assertEqual(settings.providers.dictation_model, original.providers.dictation_model)
        self.assertEqual(settings.limits, original.limits)

    def test_low_budget_times_out_explicitly_without_running_inference(self):
        settings = create_settings(repository_root=Path.cwd(), environ={})
        controller = ResourceController(settings, budget_reader=lambda reserve: GPUBudget(32000, 31000, reserve))
        ran = []
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            with controller.inference("test:model", timeout=0.03):
                ran.append(True)
        self.assertEqual(ran, [])
        self.assertEqual(controller.last_admission, "waiting_for_gpu_budget")

    def test_shared_admission_never_sends_model_eviction_requests(self):
        settings = create_settings(repository_root=Path.cwd(), environ={})
        controller = ResourceController(settings, budget_reader=lambda reserve: GPUBudget(32000, 31000, reserve))
        with patch("fusion_reader_v2.resources.urlopen") as request:
            with self.assertRaises(TimeoutError):
                with controller.inference("test:model", timeout=0.02):
                    self.fail("must not run")
        request.assert_not_called()

    def test_resource_status_does_not_probe_hardware_on_every_poll(self):
        from tests.helpers import test_app

        app = test_app()
        settings = create_settings(repository_root=Path.cwd(), environ={})
        with patch("fusion_reader_v2.resources.read_gpu_budget") as read:
            controller = ResourceController(settings, budget_reader=read)
            status = controller.status(app)
        read.assert_not_called()
        self.assertFalse(status["gpu"]["measured"])
        self.assertTrue(status["estimates_are_not_peak_vram_guarantees"])

    def test_expired_measurement_cannot_start_native_work_after_its_deadline(self):
        import time

        settings = create_settings(repository_root=Path.cwd(), environ={})

        def delayed(reserve):
            time.sleep(0.03)
            return GPUBudget(32000, 0, reserve)

        controller = ResourceController(settings, budget_reader=delayed)
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            with controller.inference("test:model", timeout=0.01):
                self.fail("expired sample must not admit inference")
