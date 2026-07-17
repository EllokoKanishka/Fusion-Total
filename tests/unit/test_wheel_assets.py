from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.check_wheel_assets import REQUIRED_ASSETS, missing_assets


class WheelAssetTests(unittest.TestCase):
    def test_complete_wheel_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "fusion_reader_v2-test.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name in REQUIRED_ASSETS:
                    archive.writestr(name, b"asset")
            self.assertEqual(missing_assets(wheel), [])

    def test_missing_nested_module_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "fusion_reader_v2-test.whl"
            missing = "fusion_reader_v2/web/static/js/bootstrap.mjs"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name in REQUIRED_ASSETS - {missing}:
                    archive.writestr(name, b"asset")
            self.assertEqual(missing_assets(wheel), [missing])

    def test_invalid_or_missing_wheel_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.whl"
            self.assertEqual(missing_assets(missing), [f"wheel_missing:{missing}"])
            invalid = root / "invalid.whl"
            invalid.write_text("not a zip", encoding="utf-8")
            self.assertEqual(missing_assets(invalid), [f"wheel_invalid:{invalid}:BadZipFile"])


if __name__ == "__main__":
    unittest.main()
