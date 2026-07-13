from __future__ import annotations

import re
import unittest
from pathlib import Path


class CSPFrontendTests(unittest.TestCase):
    def test_static_ui_has_no_inline_script_style_or_event_handlers(self) -> None:
        root = Path(__file__).resolve().parents[2]
        html = (root / "fusion_reader_v2/web/static/index.html").read_text(encoding="utf-8")
        javascript = (root / "fusion_reader_v2/web/static/app.js").read_text(encoding="utf-8")
        self.assertNotIn("unsafe-inline", html)
        self.assertNotRegex(html, r"\sstyle\s*=")
        self.assertNotRegex(html, r"\son(?:click|change|input|load|error|keydown)\s*=")
        self.assertNotRegex(html, r"<script(?![^>]+\bsrc=)[^>]*>")
        self.assertNotRegex(javascript, r"\.style\.")

    def test_server_csp_rejects_inline_code(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (root / "fusion_reader_v2/web/server.py").read_text(encoding="utf-8")
        csp = re.search(r'"default-src \'self\'.+?img-src \'self\' data:",', source, re.DOTALL)
        self.assertIsNotNone(csp)
        self.assertNotIn("unsafe-inline", csp.group(0))


if __name__ == "__main__":
    unittest.main()
