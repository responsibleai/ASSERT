# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline coverage for the bank-manager smoke-check reporter."""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "examples/bank_manager_agent_control/scripts/smoke_test.py"
)


class SmokeCheckTimingTests(unittest.TestCase):
    def setUp(self):
        original_path = sys.path[:]
        try:
            spec = importlib.util.spec_from_file_location("bank_smoke_timing", SCRIPT)
            self.smoke = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.smoke)
        finally:
            sys.path[:] = original_path

    def run_check(self, exception=None):
        now = [10.0]
        calls = []

        def work():
            calls.append("called")
            now[0] = 10.125
            if exception is not None:
                raise exception

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("time.perf_counter", side_effect=lambda: now[0]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.smoke.check("synthetic check", work)
        self.assertEqual(calls, ["called"])
        return stdout.getvalue(), stderr.getvalue()

    def test_success_reports_callable_duration_and_counts_one_pass(self):
        stdout, stderr = self.run_check()
        self.assertEqual(stdout, "  PASS  synthetic check (0.125s)\n")
        self.assertEqual(stderr, "")
        self.assertEqual((self.smoke._passed, self.smoke._failed), (1, 0))

    def test_assertion_failure_reports_duration_and_preserves_error(self):
        stdout, stderr = self.run_check(AssertionError("expected failure"))
        self.assertEqual(stdout, "  FAIL  synthetic check: expected failure (0.125s)\n")
        self.assertEqual(stderr, "")
        self.assertEqual((self.smoke._passed, self.smoke._failed), (0, 1))

    def test_unexpected_exception_reports_duration_and_preserves_traceback(self):
        stdout, stderr = self.run_check(ValueError("unexpected failure"))
        self.assertEqual(
            stdout, "  FAIL  synthetic check: ValueError: unexpected failure (0.125s)\n"
        )
        self.assertIn("Traceback (most recent call last):", stderr)
        self.assertIn("ValueError: unexpected failure", stderr)
        self.assertEqual((self.smoke._passed, self.smoke._failed), (0, 1))

    def test_skip_remains_untimed_and_does_not_change_pass_fail_counts(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.smoke.skip("optional dependency", "not installed")
        self.assertEqual(stdout.getvalue(), "  SKIP  optional dependency: not installed\n")
        self.assertEqual(
            (self.smoke._passed, self.smoke._failed, self.smoke._skipped), (0, 0, 1)
        )


if __name__ == "__main__":
    unittest.main()
