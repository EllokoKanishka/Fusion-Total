import unittest
from pathlib import Path


class SmokeScriptTests(unittest.TestCase):
    def test_smoke_script_exists_and_has_expected_sections(self):
        text = Path("scripts/smoke_fusion_reader_v2.sh").read_text(encoding="utf-8")
        for token in (
            "FUSION FILE CHECKS",
            "FUSION PORT CHECKS",
            "BOUNDARY CHECKS",
            "VERIFY INTEGRATION",
            "FINAL RESULT",
            "7852",
            "7853",
            "7854",
            "8010",
            "8021",
        ):
            self.assertIn(token, text)

    def test_smoke_script_avoids_dangerous_commands(self):
        text = Path("scripts/smoke_fusion_reader_v2.sh").read_text(encoding="utf-8")
        for forbidden in ("fuser -k", "pkill", "killall", "rm -rf", "git push", "gh pr merge"):
            self.assertNotIn(forbidden, text)

    def test_verify_warning_results_are_preserved(self):
        text = Path("scripts/smoke_fusion_reader_v2.sh").read_text(encoding="utf-8")
        warning_case = "OK_WITH_WARNINGS|OK_WITH_STRICT_WARNINGS|OK_WITH_EXTERNAL_WARNINGS)"
        self.assertIn(warning_case, text)
        self.assertIn('warn "verify_voice_port_isolation.sh reported FINAL RESULT: ${verify_result}"', text)
        self.assertIn("unknown or missing FINAL RESULT", text)
        self.assertNotIn('ok "verify_voice_port_isolation.sh completed without strict failure"', text)
