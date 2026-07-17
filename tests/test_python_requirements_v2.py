import unittest
from pathlib import Path


class PythonRequirementsV2Tests(unittest.TestCase):
    def test_core_requirements_exists_and_lists_expected_packages(self):
        path = Path("requirements/fusion-reader-v2.txt")
        self.assertTrue(path.exists(), "missing core requirements file")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Pillow", text)
        self.assertIn("python-docx", text)
        self.assertNotIn("7852", text)
        self.assertNotIn("7853", text)
        self.assertNotIn("7854", text)
        self.assertNotIn("/home/", text)
        lowered = text.casefold()
        for forbidden in (
            "doctora",
            "antigravity",
            "telegram",
            "openclaw",
            "curl",
            "ffmpeg",
            "pdftotext",
            "tesseract",
            "ss",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_optional_requirements_are_documented_and_conservative(self):
        path = Path("requirements/fusion-reader-v2-optional.txt")
        self.assertTrue(path.exists(), "missing optional requirements file")
        text = path.read_text(encoding="utf-8")
        self.assertIn("faster-whisper", text)
        self.assertIn("openai-whisper", text)
        self.assertNotIn("/home/", text)
        self.assertNotIn("7853", text)
        lowered = text.casefold()
        self.assertNotIn("doctora", lowered)
        docs = Path("docs/DEPENDENCIES_V2.md").read_text(encoding="utf-8")
        self.assertIn("requirements/fusion-reader-v2.txt", docs)
        self.assertIn("requirements/fusion-reader-v2-optional.txt", docs)


if __name__ == "__main__":
    unittest.main()
