from __future__ import annotations

import unittest

from scripts.check_coverage_thresholds import CRITICAL, evaluate, percent


def report(value: int = 100) -> dict:
    summary = {"covered_lines": value, "num_statements": 100, "covered_branches": value, "num_branches": 100}
    return {"totals": dict(summary), "files": {name: {"summary": dict(summary)} for name in CRITICAL}}


class CoverageThresholdTests(unittest.TestCase):
    def test_success_and_zero_division(self) -> None:
        self.assertEqual(evaluate(report()), [])
        self.assertEqual(percent(0, 0), 100.0)

    def test_global_and_critical_failures_are_named(self) -> None:
        data = report()
        data["totals"]["covered_lines"] = 20
        target = next(iter(CRITICAL))
        data["files"][target]["summary"]["covered_branches"] = 10
        failures = evaluate(data)
        self.assertTrue(any("global lines" in item for item in failures))
        self.assertTrue(any(target in item and "branches" in item for item in failures))

    def test_missing_file_and_invalid_shape(self) -> None:
        data = report()
        target = next(iter(CRITICAL))
        del data["files"][target]
        self.assertTrue(any("missing" in item for item in evaluate(data)))
        self.assertTrue(evaluate({}))


if __name__ == "__main__":
    unittest.main()
