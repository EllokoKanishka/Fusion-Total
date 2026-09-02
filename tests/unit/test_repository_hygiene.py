from __future__ import annotations

import unittest

from scripts.check_repository_hygiene import check


class RepositoryHygieneTests(unittest.TestCase):
    def test_repository_tree_passes_hygiene_policy(self) -> None:
        self.assertEqual(check(), [])

    def test_runtime_generated_and_misplaced_files_are_rejected(self) -> None:
        failures = check(
            [
                "runtime/fusion_reader_v2/session.json",
                "fusion_reader_v2/__pycache__/reader.pyc",
                "config/n8n.json",
                "task.md",
                "requirements/core.txt",
            ]
        )
        self.assertEqual(
            failures,
            [
                "active_autonomy_config:config/n8n.json",
                "generated_artifact:fusion_reader_v2/__pycache__/reader.pyc",
                "duplicate_requirement_manifest:requirements/core.txt",
                "tracked_runtime:runtime/fusion_reader_v2/session.json",
                "historical_root_file:task.md",
            ],
        )

    def test_archived_material_is_allowed(self) -> None:
        self.assertEqual(
            check(
                [
                    "docs/archive/FUSION_READER_V2_BLUEPRINT.md",
                    "legacy/config/autonomy_stack/n8n.json",
                ]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
