from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SubprocessArchitectureTests(unittest.TestCase):
    def test_active_package_only_spawns_through_owned_subprocess(self) -> None:
        root = Path(__file__).resolve().parents[2] / "fusion_reader_v2"
        violations: list[str] = []
        for path in root.rglob("*.py"):
            if path.name == "owned_subprocess.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                    if node.func.attr in {"run", "Popen"}:
                        violations.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
