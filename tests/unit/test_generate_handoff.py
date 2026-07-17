from __future__ import annotations

import unittest

from scripts.generate_handoff import OUTPUTS, render


class GenerateHandoffTests(unittest.TestCase):
    def test_render_labels_git_local_github_and_human_evidence(self) -> None:
        data = {
            "generated_at": "2026-07-13T00:00:00+00:00",
            "git": {
                "branch": "refactor/v2-total-consolidation",
                "head_observed_before_generation": "a" * 40,
                "base_ref": "origin/main",
                "base": "b" * 40,
                "commit_count": 2,
                "changed_files": 3,
                "additions": 10,
                "deletions": 4,
                "commits": ["abc change"],
                "files": ["fusion_reader_v2/facade.py"],
            },
            "local": {
                "tests": "545 tests OK",
                "coverage": {"state": "observed_local", "lines": 91.0, "branches": 81.0},
                "python": "3.12.0",
                "node": "v22.0.0",
                "package_version": "2.0.0",
            },
            "github": {"state_source": "observed_github", "state": "OPEN", "isDraft": True},
            "human": {"license": "pending", "branch_protection": "pending", "merge": "not performed"},
            "risks": ["Nightly pending."],
        }
        outputs = render(data)
        self.assertEqual(set(outputs), set(OUTPUTS.values()))
        handoff = outputs[OUTPUTS["handoff"]]
        self.assertIn("Head observed before generation (Git)", handoff)
        self.assertIn("Observed locally", handoff)
        self.assertIn("Observed on GitHub", handoff)
        self.assertIn("Human checklist", handoff)
        self.assertIn("Nightly pending.", handoff)
        self.assertIn("545 tests OK", handoff)


if __name__ == "__main__":
    unittest.main()
