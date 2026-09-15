"""검사 누락·실패·시간 초과를 성공으로 보고하지 않는지 검사한다."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tests = self.root / "tools/tests"
        self.tests.mkdir(parents=True)
        (self.tests / "test_example.py").write_text("# fixture\n", encoding="utf-8")

    def test_default_dialogue_plan_does_not_run_worker_or_generation(self):
        plan = check.plan("dialogue", sys.executable, self.root)
        self.assertEqual([row["name"] for row in plan], ["harness", "server"])
        self.assertTrue(all(row["command"][2:5] == ["-m", "unittest", "discover"] for row in plan))
        all_plan = check.plan("all", sys.executable, self.root)
        self.assertEqual(len({row["name"] for row in all_plan}), len(all_plan))
        self.assertIn("web", [row["name"] for row in all_plan])
        self.assertIn("web", [row["name"] for row in check.plan("platform", sys.executable, self.root)])

    def test_failure_returns_nonzero_and_keeps_report_and_log(self):
        output = self.root / "result"
        process = subprocess.CompletedProcess([], 1, "", "Ran 3 tests in 0.1s\nFAILED (failures=1)\n")
        with patch.object(check, "git_state", return_value={}), patch.object(check.subprocess, "run", return_value=process):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check.execute("harness", sys.executable, output, root=self.root), 1)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertEqual(report["tests_run"], 3)
        self.assertIn("FAILED", (output / "harness.log").read_text())

    def test_missing_tests_zero_tests_and_all_skipped_are_failures(self):
        output = self.root / "result"
        output.mkdir()
        suite = check.plan("harness", sys.executable, self.root)[0]
        for text in ["Ran 0 tests in 0.0s\nOK\n", "Ran 2 tests in 0.0s\nOK (skipped=2)\n"]:
            with patch.object(check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", text)):
                self.assertFalse(check.run_suite(suite, self.root, output, 5)["passed"])
        suite["pattern"] = "test_missing_*.py"
        with patch.object(check.subprocess, "run") as launch:
            self.assertEqual(check.run_suite(suite, self.root, output, 5)["error"], "no_test_files")
            launch.assert_not_called()

    def test_timeout_keeps_partial_output_and_cannot_pass(self):
        output = self.root / "result"
        output.mkdir()
        suite = check.plan("harness", sys.executable, self.root)[0]
        with patch.object(check.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 1, output=b"partial output")):
            row = check.run_suite(suite, self.root, output, 1)
        self.assertEqual(row["error"], "timeout")
        self.assertFalse(row["passed"])
        self.assertIn("partial output", (output / row["log"]).read_text())

    def test_existing_output_directory_is_preserved(self):
        output = self.root / "result"
        output.mkdir()
        sentinel = output / "report.json"
        sentinel.write_text("previous result", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            check.execute("harness", sys.executable, output, root=self.root)
        self.assertEqual(sentinel.read_text(), "previous result")


if __name__ == "__main__":
    unittest.main()
