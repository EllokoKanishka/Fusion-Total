from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import subprocess
import sys
import tempfile
import threading
import unittest

from PIL import Image, ImageDraw
from fusion_reader_v2.config import ConfigurationError, ResourceSettings, create_settings
from fusion_reader_v2 import documents
from fusion_reader_v2.owned_subprocess import OwnedProcessError, run_owned


class ConsolidationQualityTests(unittest.TestCase):
    def page(self, heading=True, lines=5, three=False, weak=False):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        if heading:
            draw.rectangle((60, 60, 540, 75), fill="black")
        for index in range(lines):
            y = 240 + index * 55
            if three:
                for left, right in [(60, 180), (240, 360), (420, 540)]:
                    draw.rectangle((left, y, right, y + 9), fill="black")
            else:
                draw.rectangle((60, y, 270, y + 9), fill="black")
                if not weak or index == 0:
                    draw.rectangle((330, y, 540, y + (0 if weak else 9)), fill="black")
        return image

    def test_ocr_detects_supported_columns_without_losing_heading(self):
        regions = documents._ocr_column_regions(self.page())
        self.assertEqual(len(regions), 3)
        self.assertEqual(regions[0][:2], (0, 0))
        self.assertLess(regions[1][2], regions[2][0])
        self.assertEqual(len(documents._ocr_column_regions(self.page(heading=False))), 2)

    def test_ocr_ambiguous_layouts_keep_full_page(self):
        for image in (
            Image.new("RGB", (100, 100), "white"),
            Image.new("RGB", (600, 800), "white"),
            self.page(three=True),
            self.page(lines=1),
            self.page(weak=True),
        ):
            with self.subTest(size=image.size):
                self.assertIsNone(documents._ocr_column_regions(image))

    def test_ocr_regions_preserve_order_and_empty_regions_do_not_erase_text(self):
        with tempfile.TemporaryDirectory() as folder:
            image = self.page()
            path = Path(folder) / "page.png"
            image.save(path)
            with patch.object(documents, "run_tesseract_plain", side_effect=["Heading", "", "Right text"]):
                self.assertEqual(documents.structured_ocr_image(path, "fake-tesseract"), "Heading\n\nRight text")
            with (
                patch.object(documents, "_ocr_column_regions", return_value=None),
                patch.object(documents, "run_tesseract_plain", return_value="Full text") as recognize,
            ):
                self.assertEqual(documents.structured_ocr_image(path, "fake-tesseract"), "Full text")
                self.assertEqual(recognize.call_args.kwargs["psm"], 3)
            crop = Path(folder) / "crop.png"
            documents.save_ocr_crop(image, (10, 20, 110, 70), crop, upscale=2)
            with Image.open(crop) as result:
                self.assertEqual(result.size, (200, 100))

    def test_resource_configuration_rejects_invalid_ownership_and_nonfinite_direct_settings(self):
        for values in ({"ollama_mode": "invalid"}, {"private_ollama_port": 11434}, {"reserve_mib": -1}):
            with self.assertRaises(ConfigurationError):
                ResourceSettings(**values).validate()
        settings = create_settings(environ={})
        for values in (
            {"chat_provider": "invalid"},
            {"openai_chat_execution_mode": "invalid"},
            {"openai_chat_agent_dir": Path("/wrong/agent")},
            {"tts_timeout_seconds": float("nan")},
        ):
            with self.assertRaises(ConfigurationError):
                replace(settings, providers=replace(settings.providers, **values)).validate()

    def test_owned_process_preflight_never_spawns_and_check_propagates_failure(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("fusion_reader_v2.owned_subprocess.subprocess.Popen") as spawn:
            with self.assertRaises(ValueError):
                run_owned(["unused"], timeout=1, output_limit=-1)
            with self.assertRaises(OwnedProcessError):
                run_owned(["unused"], timeout=1, cancel_event=cancelled)
            spawn.assert_not_called()
        with self.assertRaises(subprocess.CalledProcessError):
            run_owned([sys.executable, "-c", "raise SystemExit(7)"], timeout=2, check=True)
