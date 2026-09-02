from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.check_wheel_assets import REQUIRED_ASSETS, discover_static_assets, missing_assets


class WheelAssetTests(unittest.TestCase):
    def test_asset_manifest_discovers_every_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static = root / "fusion_reader_v2/web/static"
            (static / "js").mkdir(parents=True)
            (static / "index.html").write_text("reader", encoding="utf-8")
            (static / "js/new_module.mjs").write_text("export {};", encoding="utf-8")
            self.assertEqual(
                discover_static_assets(root),
                {
                    "fusion_reader_v2/web/static/index.html",
                    "fusion_reader_v2/web/static/js/new_module.mjs",
                },
            )

    def test_current_manifest_covers_media_dictation_and_brand_asset(self) -> None:
        for relative in (
            "panda-fusion-emblem.webp",
            "js/dictation.mjs",
            "js/media.mjs",
        ):
            self.assertIn(f"fusion_reader_v2/web/static/{relative}", REQUIRED_ASSETS)

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
