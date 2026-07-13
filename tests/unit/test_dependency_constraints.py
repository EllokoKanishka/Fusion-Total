from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_dependency_constraints import check, constraint_names


class DependencyConstraintTests(unittest.TestCase):
    def test_repository_constraints_cover_core_and_dev(self) -> None:
        self.assertEqual(check(), [])

    def test_unpinned_constraint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "constraints.txt"
            path.write_text("Pillow>=12\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "constraint_not_pinned"):
                constraint_names(path)


if __name__ == "__main__":
    unittest.main()
