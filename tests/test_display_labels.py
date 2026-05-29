"""Unit tests for ``assert_eval.display`` and CLI terminology regression guards."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from assert_eval import display


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_PATH = REPO_ROOT / "assert_eval" / "cli.py"
RUNNER_PATH = REPO_ROOT / "assert_eval" / "runner.py"

# Substrings that must not appear inside string literals in the CLI surface.
# Each is paired with an explanation so a future failure tells the maintainer
# what to replace it with. Restricted to forms the viewer doesn't use.
FORBIDDEN_CLI_STRINGS: tuple[tuple[str, str], ...] = (
    ("Prompt Viol.", "Use the full viewer wording for the policy-violation column."),
    ("Prompt Overref.", "Use the full viewer wording for the overrefusal column."),
    ("Scenario Viol.", "Use the full viewer wording for the policy-violation column."),
    ("J.Fail%", "Use the full viewer wording for the judge-failure column."),
    ("Bad Event Rate", "Match the viewer's per-dimension column header."),
    ("Value Mix", "Match the viewer's per-dimension breakdown column header."),
    ("Auditor", "Post-PR #23 the customer term is 'tester'."),
    ("Auditing", "Post-PR #23 the customer term is 'testing'."),
    ("Top Behavior Category Deltas", "Use the viewer's compare-section heading."),
)


class DisplayLabelTest(unittest.TestCase):
    def test_status_labels_match_viewer(self) -> None:
        # Locked to the viewer's statusConfig in routes/+page.svelte.
        self.assertEqual(display.label_status("systematized"), "Behavior Categories Defined")
        self.assertEqual(
            display.label_status("test_set_ready"),
            "Evaluation Test Set Generated",
        )
        self.assertEqual(display.label_status("has_results"), "Has Evaluation Result")

    def test_status_fallback_for_unknown_value(self) -> None:
        self.assertEqual(display.label_status("some_new_state"), "Some new state")
        self.assertEqual(display.label_status(None), "—")
        self.assertEqual(display.label_status(""), "—")

    def test_run_status_labels_match_viewer_header(self) -> None:
        self.assertEqual(display.label_run_status("running"), "Running")
        self.assertEqual(display.label_run_status("completed"), "Completed")
        self.assertEqual(display.label_run_status("failed"), "Failed")
        self.assertEqual(display.label_run_status("abandoned"), "Abandoned")
        self.assertEqual(display.label_run_status(None), "—")

    def test_stage_labels_match_viewer(self) -> None:
        # Locked to the monitor page's stageLabels map.
        self.assertEqual(display.label_stage("systematize"), "Behavior Categories Generation")
        self.assertEqual(display.label_stage("taxonomy"), "Behavior Categories Generation")
        self.assertEqual(display.label_stage("test_set"), "Test Set Generation")
        self.assertEqual(display.label_stage("inference"), "Inference")
        self.assertEqual(display.label_stage("judge"), "Scoring")
        self.assertEqual(display.label_stage("systematization"), "Systematization")
        self.assertEqual(
            display.label_stage("systematization_convert"),
            "Behavior Categories Conversion",
        )

    def test_stage_status_labels(self) -> None:
        self.assertEqual(display.label_stage_status("completed"), "Complete")
        self.assertEqual(display.label_stage_status("running"), "Running")
        self.assertEqual(display.label_stage_status("pending"), "Pending")
        # The viewer normalizes manifest ``failed`` per-stage values to
        # ``error`` before rendering, so both keys collapse to the same
        # CLI-facing label here.
        self.assertEqual(display.label_stage_status("failed"), "Error")
        self.assertEqual(display.label_stage_status("error"), "Error")
        self.assertEqual(display.label_stage_status(None), "—")

    def test_metric_labels(self) -> None:
        self.assertEqual(display.label_metric("policy_violation"), "Policy violation")
        self.assertEqual(display.label_metric("overrefusal"), "Overrefusal")
        self.assertEqual(display.label_metric("harm_actionability"), "Harm actionability")
        self.assertEqual(display.label_metric("judge_failure_rate"), "Judge failure rate")
        # Unknown metric falls back to snake-to-sentence, matching the viewer's
        # default metricLabel behavior.
        self.assertEqual(display.label_metric("custom_dimension"), "Custom dimension")
        self.assertEqual(display.label_metric(None), "—")


class CliTerminologyGuardTest(unittest.TestCase):
    """Fail loudly if a legacy or short-form label reappears in the CLI.

    A green pytest run is what proves issue #58 has not regressed; this guard
    catches the most common regression — someone re-adding ``"Prompt Viol."``
    to a Rich table without thinking about whether the viewer has a column by
    that name.
    """

    def _strings_in(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_cli_does_not_use_legacy_labels(self) -> None:
        text = self._strings_in(CLI_PATH)
        for needle, reason in FORBIDDEN_CLI_STRINGS:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text, msg=reason)

    def test_runner_does_not_use_legacy_labels(self) -> None:
        text = self._strings_in(RUNNER_PATH)
        for needle, reason in FORBIDDEN_CLI_STRINGS:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text, msg=reason)

    def test_cli_renders_status_via_display_module(self) -> None:
        """Status strings should go through ``label_status``, not be hard-coded.

        Heuristic: the literal ``"systematized"`` may appear as a key but not
        as a user-facing rendering. Once :mod:`assert_eval.display` is wired up,
        ``cli.py`` calls ``label_status`` somewhere.
        """
        text = self._strings_in(CLI_PATH)
        self.assertRegex(
            text,
            r"label_status\s*\(",
            msg="cli.py should call assert_eval.display.label_status for suite statuses.",
        )

    def test_cli_renders_run_status_via_display_module(self) -> None:
        text = self._strings_in(CLI_PATH)
        self.assertRegex(
            text,
            r"label_run_status\s*\(",
            msg="cli.py should call assert_eval.display.label_run_status for run statuses.",
        )

    def test_cli_renders_stages_via_display_module(self) -> None:
        text = self._strings_in(CLI_PATH)
        # Either label_stage or label_stage_status must be used.
        self.assertTrue(
            re.search(r"label_stage(_status)?\s*\(", text),
            msg="cli.py should call assert_eval.display.label_stage(_status) for stage names.",
        )


if __name__ == "__main__":
    unittest.main()
