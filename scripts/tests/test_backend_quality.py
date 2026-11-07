import json
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts import backend_quality


class RuffBaselineTests(unittest.TestCase):
    @patch.object(
        backend_quality,
        "subprocess",
        **{
            "run.return_value": subprocess.CompletedProcess(
                [], 0, stdout="not json", stderr=""
            )
        },
    )
    def test_ruff_lint_counts_reports_invalid_json(self, mock_subprocess):
        with patch("sys.stderr") as stderr:
            counts, returncode = backend_quality.ruff_lint_counts([])

        self.assertFalse(counts)
        self.assertEqual(returncode, 1)
        self.assertIn(
            "Unable to parse Ruff JSON output", stderr.write.call_args_list[0].args[0]
        )
        mock_subprocess.run.assert_called_once()

    def test_load_baseline_reports_missing_file(self):
        with patch.object(
            backend_quality, "BASELINE", Path("missing-ruff-baseline.json")
        ):
            with self.assertRaisesRegex(ValueError, "Unable to load Ruff baseline"):
                backend_quality.load_baseline()

    def test_load_baseline_reports_invalid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
        ) as stream:
            stream.write("not json")
            path = Path(stream.name)
        try:
            with patch.object(backend_quality, "BASELINE", path):
                with self.assertRaisesRegex(ValueError, "Invalid Ruff baseline"):
                    backend_quality.load_baseline()
        finally:
            path.unlink(missing_ok=True)

    @patch.object(
        backend_quality, "load_baseline", side_effect=ValueError("baseline test error")
    )
    @patch.object(backend_quality, "ruff_lint_counts", return_value=(Counter(), 0))
    def test_run_ruff_lint_reports_baseline_error(self, mock_counts, mock_baseline):
        with patch("sys.stderr") as stderr:
            result = backend_quality.run_ruff_lint([])

        self.assertEqual(result, 1)
        self.assertIn("baseline test error", stderr.write.call_args_list[0].args[0])
        mock_counts.assert_called_once_with([])
        mock_baseline.assert_called_once_with()

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
        baseline = Counter(
            {"app/example.py|E501|value = 1": 2, "app/example.py|I001|import x": 1}
        )
        moved = Counter(
            {"app/example.py|E501|value = 1": 2, "app/example.py|I001|import x": 1}
        )
        self.assertFalse(backend_quality.new_lint_violations(moved, baseline))

    def test_baseline_is_reviewable_json(self):
        baseline = Path(backend_quality.BASELINE)
        data = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertEqual(data["strategy"], "file-rule-source-fingerprint")
        self.assertIn("violations", data)

    def test_format_check_does_not_modify_a_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            dir=backend_quality.BACKEND,
            delete=False,
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

    @patch.object(backend_quality, "run_command", return_value=0)
    @patch.object(backend_quality, "run_ruff_lint", return_value=0)
    def test_run_ruff_forwards_only_supplied_filenames(self, mock_lint, mock_run):
        supplied = ["backend/app/example.py", "backend/tests/test_example.py"]

        self.assertEqual(backend_quality.run_ruff(supplied), 0)
        mock_lint.assert_called_once_with(supplied)
        command = mock_run.call_args.args[0]
        self.assertEqual(command[-len(supplied) :], supplied)
        self.assertNotIn("backend/app/unrelated.py", command)


if __name__ == "__main__":
    unittest.main()
