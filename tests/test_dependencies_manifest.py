import unittest
from pathlib import Path


class DependencyManifestTests(unittest.TestCase):
    def test_dependencies_manifest_exists_and_covers_critical_ports(self):
        text = Path("docs/DEPENDENCIES_V2.md").read_text(encoding="utf-8")
        self.assertIn("# Fusion Reader v2 Dependencies", text)
        for token in ("8010", "8021", "7851", "7852", "7853", "7854"):
            self.assertIn(token, text)
        self.assertIn("Fusion no usa `7854`", text)
        self.assertIn("Fusion no usa `7852`", text)

    def test_dependencies_manifest_marks_external_boundaries(self):
        text = Path("docs/DEPENDENCIES_V2.md").read_text(encoding="utf-8")
        self.assertIn("Doctora Lucy es externa a este repo.", text)
        self.assertIn("OpenClaw `main` no debe tocarse", text)
        self.assertIn("Warnings documentales o de memoria de Doctora no implican que Fusion esté", text)
        self.assertIn("roto.", text)
