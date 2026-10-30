import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scripts import backend_quality


class RuffBaselineTests(unittest.TestCase):
    def test_existing_violations_are_allowed(self):
        baseline = Counter({"app/example.py|E501|value = 1": 2})
        self.assertFalse(backend_quality.new_lint_violations(baseline, baseline))

    def test_new_violation_fails(self):
        baseline = Counter({"app/example.py|E501|value = 1": 2})
        current = Counter({"app/example.py|E501|value = 1": 3})
        self.assertEqual(
            backend_quality.new_lint_violations(current, baseline),
            Counter({"app/example.py|E501|value = 1": 1}),
        )

    def test_removing_a_violation_passes(self):
        baseline = Counter({"app/example.py|E501|value = 1": 2})
        current = Counter({"app/example.py|E501|value = 1": 1})
        self.assertFalse(backend_quality.new_lint_violations(current, baseline))

    def test_line_movement_does_not_change_rule_counts(self):
        baseline = Counter({"app/example.py|E501|value = 1": 2, "app/example.py|I001|import x": 1})
        moved = Counter({"app/example.py|E501|value = 1": 2, "app/example.py|I001|import x": 1})
        self.assertFalse(backend_quality.new_lint_violations(moved, baseline))

    def test_baseline_is_reviewable_json(self):
        baseline = Path(backend_quality.BASELINE)
        data = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertEqual(data["strategy"], "file-rule-source-fingerprint")
        self.assertIn("violations", data)

    def test_format_check_does_not_modify_a_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=backend_quality.BACKEND, delete=False,
            encoding="utf-8",
        ) as stream:
            stream.write("value = 1\n")
            path = Path(stream.name)
        try:
            before = path.read_bytes()
            self.assertEqual(backend_quality.run_ruff([path.as_posix()]), 1)
            self.assertEqual(path.read_bytes(), before)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
