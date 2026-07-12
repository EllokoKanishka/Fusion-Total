from __future__ import annotations

import ast
import unittest
from pathlib import Path


class ActiveImportBoundaryTests(unittest.TestCase):
    def test_active_package_does_not_import_legacy_modules(self) -> None:
        forbidden = {"app", "openclaw_direct_chat", "molbot_direct_chat"}
        findings: list[str] = []
        for path in sorted(Path("fusion_reader_v2").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".", 1)[0] in forbidden:
                        findings.append(f"{path}:{node.lineno}: {name}")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
