import unittest
from pathlib import Path


class ClosureBacklogV2Tests(unittest.TestCase):
    def test_closure_document_and_primary_links(self):
        path = Path("docs/CLOSURE_AND_BACKLOG_V2.md")
        self.assertTrue(path.is_file())
        for source in ("README.md", "FUSION_READER_V2_STATE.md"):
            self.assertIn("CLOSURE_AND_BACKLOG_V2.md", Path(source).read_text(encoding="utf-8"))

    def test_closure_records_contracts_and_boundaries(self):
        text = Path("docs/CLOSURE_AND_BACKLOG_V2.md").read_text(encoding="utf-8")
        for token in (
            "P0",
            "P1",
            "P2",
            "Fuera de alcance",
            "8010",
            "7851",
            "7852",
            "7853",
            "7854",
            "8021",
            "11434",
            "8080",
            "auto",
            "server",
            "cli",
            "verify es read-only",
            "smoke es no invasivo",
            "reconciliar metadata",
        ):
            self.assertIn(token, text)
        self.assertIn("No hay bloqueos P0 confirmados", text)
        self.assertIn("no que todos los\nservicios locales estén siempre encendidos", text)

    def test_canonical_commands_are_portable_and_safe(self):
        text = Path("docs/CLOSURE_AND_BACKLOG_V2.md").read_text(encoding="utf-8")
        commands = text.split("## 9. Comandos canónicos", 1)[1]
        self.assertNotIn("/home/", commands)
        for forbidden in ("rm -rf", "gh pr merge", "git push --force", "pkill", "killall"):
            self.assertNotIn(forbidden, text)
        self.assertIn("Doctora, Antigravity, Telegram y OpenClaw `main`", text)


if __name__ == "__main__":
    unittest.main()
