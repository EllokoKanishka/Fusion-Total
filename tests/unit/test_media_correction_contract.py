from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class MediaCorrectionContractTests(unittest.TestCase):
    def test_backend_wires_request_scoped_post_correction(self) -> None:
        route = (ROOT / "fusion_reader_v2/web/routes/media.py").read_text(encoding="utf-8")
        service = (ROOT / "fusion_reader_v2/services/media.py").read_text(encoding="utf-8")
        self.assertIn('include_post_correction=selected("post_correct", False)', route)
        self.assertIn('post_correct_transcript=selected("post_correct", False)', route)
        self.assertIn("correction_requested=bool(post_correct_transcript)", service)
        self.assertIn("_correct_transcript_paragraphs", service)

    def test_frontend_exposes_opt_in_checkbox(self) -> None:
        html = (ROOT / "fusion_reader_v2/web/static/index.html").read_text(encoding="utf-8")
        js = (ROOT / "fusion_reader_v2/web/static/js/media.mjs").read_text(encoding="utf-8")
        self.assertIn('id="mediaPostCorrectionToggle"', html)
        self.assertIn("params.set('post_correct', postCorrect ? '1' : '0')", js)


if __name__ == "__main__":
    unittest.main()
