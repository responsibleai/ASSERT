# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from click.testing import CliRunner

from assert_ai.cli import (
    _has_permissibility_split,
    _violation_cells,
    _violation_column_titles,
    cli,
)
from assert_ai.results import (
    compute_policy_violation_by_permissibility,
    compute_prompt_metrics,
    has_permissibility_split_data,
)


class ResultsTest(unittest.TestCase):
    def test_compute_prompt_metrics_omits_permissible_breakdown(self) -> None:
        rows = [
            {
                "dimensions": {"behavior": "allowed-behavior"},
                "judge_status": "ok",
                "judge_error": None,
                "verdict": {
                    "dimensions": {
                        "policy_violation": False,
                        "overrefusal": True,
                    },
                    "node_judgments": [],
                },
            },
            {
                "dimensions": {"behavior": "blocked-behavior"},
                "judge_status": "ok",
                "judge_error": None,
                "verdict": {
                    "dimensions": {
                        "policy_violation": True,
                        "overrefusal": False,
                    },
                    "node_judgments": [],
                },
            },
        ]

        metrics = compute_prompt_metrics(rows)

        assert metrics is not None
        self.assertEqual(metrics["policy_violation_rate"], 0.5)
        self.assertEqual(metrics["overrefusal_rate"], 0.5)
        self.assertNotIn("permissible_overrefusal_rate", metrics)
        self.assertNotIn("not_permissible_policy_violation_rate", metrics)

    def test_compute_prompt_metrics_tolerates_disabled_builtin_dimensions(self) -> None:
        rows = [
            {
                "dimensions": {"behavior": "guarded-behavior"},
                "score_keys": ["guardrail_policy_violation"],
                "judge_status": "ok",
                "judge_error": None,
                "verdict": {
                    "dimensions": {"guardrail_policy_violation": False},
                    "node_judgments": [],
                },
            },
        ]

        metrics = compute_prompt_metrics(rows)

        assert metrics is not None
        self.assertIsNone(metrics["policy_violation_rate"])
        self.assertIsNone(metrics["overrefusal_rate"])
        self.assertEqual(metrics["dimensions"]["guardrail_policy_violation"]["rate"], 0.0)

    def test_policy_violation_by_permissibility_is_one_vote_per_row(self) -> None:
        behavior_categories = [
            {"name": "perm-a", "permissible": True},
            {"name": "perm-b", "permissible": True},
            {"name": "not-perm-a", "permissible": False},
        ]
        rows = [
            {
                "dimensions": {"behavior": "not-perm-a"},
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": True, "overrefusal": True},
                    "node_judgments": [
                        {"node_index": 0, "node_name": "perm-a", "relevant": True, "violated": True},
                        {"node_index": 1, "node_name": "perm-b", "relevant": True, "violated": False},
                        {"node_index": 2, "node_name": "not-perm-a", "relevant": False, "violated": None},
                    ]
                },
            },
            {
                "dimensions": {"behavior": "not-perm-a"},
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": True, "overrefusal": False},
                    "node_judgments": [
                        {"node_index": 0, "node_name": "perm-a", "relevant": True, "violated": False},
                        {"node_index": 2, "node_name": "not-perm-a", "relevant": True, "violated": True},
                    ]
                },
            },
            {
                # The row label is non-permissible, but only a permissible
                # behavior was violated. This is the issue #272 regression.
                "dimensions": {"behavior": "not-perm-a"},
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": True, "overrefusal": True},
                    "node_judgments": [
                        {"node_index": 1, "node_name": "perm-b", "relevant": True, "violated": True},
                        {"node_index": 2, "node_name": "not-perm-a", "relevant": True, "violated": False},
                    ]
                },
            },
        ]

        split = compute_policy_violation_by_permissibility(rows, behavior_categories)

        permissible = split["permissible"]
        not_permissible = split["not_permissible"]
        assert permissible is not None
        assert not_permissible is not None
        self.assertEqual(permissible["count"], 3)
        self.assertEqual(permissible["flagged_count"], 2)
        self.assertEqual(permissible["clear_count"], 1)
        self.assertAlmostEqual(permissible["rate"], 2 / 3)
        self.assertEqual(not_permissible["count"], 2)
        self.assertEqual(not_permissible["not_applicable_count"], 1)
        self.assertEqual(not_permissible["flagged_count"], 1)
        self.assertEqual(not_permissible["clear_count"], 1)
        self.assertAlmostEqual(not_permissible["rate"], 0.5)

        metrics = compute_prompt_metrics(rows, behavior_categories)
        assert metrics is not None
        self.assertAlmostEqual(metrics["permissible_policy_violation_rate"], 2 / 3)
        self.assertAlmostEqual(metrics["not_permissible_policy_violation_rate"], 0.5)

    def test_all_permissible_taxonomy_still_reports_the_split(self) -> None:
        """An empty impermissible bucket is a computed result, not a missing split.

        A suite that only probes permissible behavior -- the over-refusal half of
        an ACS before/after -- has no impermissible node to score, so that
        bucket's rate is ``None`` while the permissible rate is real. The split
        is still available and display surfaces must keep using it.
        """
        behavior_categories = [
            {"name": "perm-a", "permissible": True},
            {"name": "perm-b", "permissible": True},
        ]
        rows = [
            {
                "dimensions": {"behavior": "perm-a"},
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": True, "overrefusal": True},
                    "node_judgments": [
                        {"node_index": 0, "node_name": "perm-a", "relevant": True, "violated": True},
                    ],
                },
            },
            {
                "dimensions": {"behavior": "perm-b"},
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": False},
                    "node_judgments": [
                        {"node_index": 0, "node_name": "perm-a", "relevant": True, "violated": False},
                        {"node_index": 1, "node_name": "perm-b", "relevant": True, "violated": False},
                    ],
                },
            },
        ]

        split = compute_policy_violation_by_permissibility(rows, behavior_categories)

        permissible = split["permissible"]
        not_permissible = split["not_permissible"]
        assert permissible is not None
        assert not_permissible is not None
        self.assertAlmostEqual(permissible["rate"], 0.5)
        self.assertEqual(not_permissible["count"], 0)
        self.assertEqual(not_permissible["not_applicable_count"], 2)
        self.assertIsNone(not_permissible["rate"])

        metrics = compute_prompt_metrics(rows, behavior_categories)
        assert metrics is not None
        self.assertAlmostEqual(metrics["permissible_policy_violation_rate"], 0.5)
        self.assertIn("not_permissible_policy_violation_rate", metrics)
        self.assertIsNone(metrics["not_permissible_policy_violation_rate"])

        self.assertTrue(_has_permissibility_split(metrics))
        self.assertEqual(
            _violation_column_titles(True),
            (
                "Prompt impermissible violations",
                "Prompt permissible violations",
                "Scenario impermissible violations",
                "Scenario permissible violations",
            ),
        )
        self.assertEqual(_violation_cells(metrics, metrics, True), ("-", "50.0%", "-", "50.0%"))

    def test_runs_without_a_taxonomy_keep_the_superseded_pair(self) -> None:
        metrics = compute_prompt_metrics(
            [
                {
                    "dimensions": {"behavior": "anything"},
                    "judge_status": "ok",
                    "verdict": {
                        "dimensions": {"policy_violation": True, "overrefusal": False},
                        "node_judgments": [],
                    },
                }
            ]
        )

        assert metrics is not None
        self.assertNotIn("not_permissible_policy_violation_rate", metrics)
        self.assertFalse(_has_permissibility_split(metrics))
        self.assertEqual(
            _violation_column_titles(False),
            (
                "Prompt policy violations",
                "Prompt overrefusals",
                "Scenario policy violations",
            ),
        )


def _score_row(
    *,
    row_type: str,
    test_case_id: str,
    permissible_violation: bool,
    impermissible_violation: bool,
) -> dict:
    row = {
        "type": row_type,
        "test_case_id": test_case_id,
        "dimensions": {"behavior": "allowed" if permissible_violation else "blocked"},
        "target": "target-model",
        "judge_model": "judge-model",
        "judge_status": "ok",
        "verdict": {
            "dimensions": {
                "policy_violation": permissible_violation or impermissible_violation,
                "overrefusal": permissible_violation,
            },
            "node_judgments": [
                {
                    "node_index": 0,
                    "node_name": "allowed",
                    "relevant": True,
                    "violated": permissible_violation,
                },
                {
                    "node_index": 1,
                    "node_name": "blocked",
                    "relevant": True,
                    "violated": impermissible_violation,
                },
            ],
        },
    }
    if row_type == "scenario":
        row["tester_model"] = "tester-model"
    return row


def _write_split_results(results_root: Path, suite_id: str = "metrics-suite") -> None:
    suite_dir = results_root / suite_id
    suite_dir.mkdir(parents=True)
    (suite_dir / "suite.json").write_text(
        json.dumps({"created_at": "2026-08-06T00:00:00Z"}),
        encoding="utf-8",
    )
    (suite_dir / "taxonomy.json").write_text(
        json.dumps(
            {
                "behavior": {"name": "Risk"},
                "behavior_categories": [
                    {"name": "allowed", "permissible": True},
                    {"name": "blocked", "permissible": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    (suite_dir / "test_set.jsonl").write_text(
        json.dumps({"type": "prompt", "test_case_id": "prompt", "dimensions": {"behavior": "allowed"}})
        + "\n",
        encoding="utf-8",
    )

    run_rows = {
        "run-1": [
            _score_row(
                row_type="prompt",
                test_case_id="prompt-1",
                permissible_violation=True,
                impermissible_violation=False,
            ),
            _score_row(
                row_type="scenario",
                test_case_id="scenario-1",
                permissible_violation=False,
                impermissible_violation=True,
            ),
        ],
        "run-2": [
            _score_row(
                row_type="prompt",
                test_case_id="prompt-2",
                permissible_violation=False,
                impermissible_violation=True,
            ),
            _score_row(
                row_type="scenario",
                test_case_id="scenario-2",
                permissible_violation=True,
                impermissible_violation=False,
            ),
        ],
    }
    for run_id, rows in run_rows.items():
        run_dir = suite_dir / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "started_at": f"2026-08-06T00:0{run_id[-1]}:00Z",
                    "ended_at": f"2026-08-06T00:0{run_id[-1]}:30Z",
                    "stages": {"inference": "completed", "judge": "completed"},
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "scores.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )


def _make_run_judgments_stale(results_root: Path, run_id: str) -> None:
    """Make one run predate the suite taxonomy without changing legacy scores."""
    scores_path = results_root / "metrics-suite" / run_id / "scores.jsonl"
    rows = [json.loads(line) for line in scores_path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        for judgment in row["verdict"]["node_judgments"]:
            judgment["node_index"] = 100 + int(judgment["node_index"])
            judgment["node_name"] = f"stale-{judgment['node_name']}"
    scores_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _set_prompt_behavior(results_root: Path, run_id: str, behavior: str) -> None:
    scores_path = results_root / "metrics-suite" / run_id / "scores.jsonl"
    rows = [json.loads(line) for line in scores_path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if not row.get("tester_model"):
            row["dimensions"]["behavior"] = behavior
    scores_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


class ResultsCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_list_shows_both_split_metrics_for_prompts_and_scenarios(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)

            result = self.runner.invoke(
                cli,
                [
                    "results",
                    "list",
                    "--results-dir",
                    str(results_root),
                    "--suite",
                    "metrics-suite",
                    "--no-color",
                ],
                env={"COLUMNS": "220"},
                terminal_width=220,
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Prompt impermissible violations", result.output)
        self.assertIn("Prompt permissible violations", result.output)
        self.assertIn("Scenario impermissible violations", result.output)
        self.assertIn("Scenario permissible violations", result.output)

    def test_list_falls_back_when_any_run_lacks_current_split_data(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)
            _make_run_judgments_stale(results_root, "run-1")

            result = self.runner.invoke(
                cli,
                [
                    "results",
                    "list",
                    "--results-dir",
                    str(results_root),
                    "--suite",
                    "metrics-suite",
                    "--no-color",
                ],
                env={"COLUMNS": "220"},
                terminal_width=220,
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Prompt policy violations", result.output)
        self.assertIn("Prompt overrefusals", result.output)
        self.assertNotIn("Prompt impermissible violations", result.output)

    def test_run_detail_defaults_to_split_metrics_but_json_keeps_legacy_metrics(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)
            args = [
                "results",
                "status",
                "metrics-suite",
                "run-1",
                "--results-dir",
                str(results_root),
            ]

            text_result = self.runner.invoke(cli, [*args, "--no-color"], terminal_width=180)
            json_result = self.runner.invoke(cli, [*args, "--json"])

        self.assertEqual(text_result.exit_code, 0, text_result.output)
        self.assertIn("Impermissible behavior violated", text_result.output)
        self.assertIn("Permissible behavior violated", text_result.output)
        self.assertNotIn("Policy violation", text_result.output)
        self.assertNotIn("Overrefusal", text_result.output)

        self.assertEqual(json_result.exit_code, 0, json_result.output)
        payload = json.loads(json_result.output)
        prompt = payload["prompt_metrics"]
        self.assertIn("policy_violation_rate", prompt)
        self.assertIn("overrefusal_rate", prompt)
        self.assertIn("not_permissible_policy_violation_rate", prompt)
        self.assertIn("permissible_policy_violation_rate", prompt)

    def test_run_detail_falls_back_when_current_taxonomy_matches_no_judgments(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)
            _make_run_judgments_stale(results_root, "run-1")

            result = self.runner.invoke(
                cli,
                [
                    "results",
                    "status",
                    "metrics-suite",
                    "run-1",
                    "--results-dir",
                    str(results_root),
                    "--no-color",
                ],
                terminal_width=180,
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Policy violation", result.output)
        self.assertIn("Overrefusal", result.output)
        self.assertNotIn("Impermissible behavior violated", result.output)
        self.assertNotIn("Permissible behavior violated", result.output)

    def test_compare_defaults_to_impermissible_split_and_accepts_permissible_split(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)
            base_args = [
                "results",
                "compare",
                "metrics-suite",
                "run-1",
                "run-2",
                "--results-dir",
                str(results_root),
                "--no-color",
            ]

            default_result = self.runner.invoke(cli, base_args, terminal_width=180)
            permissible_result = self.runner.invoke(
                cli,
                [*base_args, "--metric", "policy_violation_permissible"],
                terminal_width=180,
            )

        self.assertEqual(default_result.exit_code, 0, default_result.output)
        self.assertIn("Run Comparison (metrics-suite, Impermissible behavior violated)", default_result.output)
        self.assertIn("0.0%", default_result.output)
        self.assertIn("100.0%", default_result.output)

        self.assertEqual(permissible_result.exit_code, 0, permissible_result.output)
        self.assertIn("Run Comparison (metrics-suite, Permissible behavior violated)", permissible_result.output)

    def test_compare_falls_back_when_any_run_lacks_current_split_data(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)
            _make_run_judgments_stale(results_root, "run-1")
            _set_prompt_behavior(results_root, "run-2", "allowed")

            result = self.runner.invoke(
                cli,
                [
                    "results",
                    "compare",
                    "metrics-suite",
                    "run-1",
                    "run-2",
                    "--results-dir",
                    str(results_root),
                    "--no-color",
                ],
                terminal_width=180,
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Run Comparison (metrics-suite, Policy violation)", result.output)
        self.assertIn("Top behavior category deltas", result.output)

    def test_empty_bucket_does_not_hide_a_valid_one_sided_split(self) -> None:
        metrics = {
            "policy_violation_on_permissible": {"count": 1, "rate": 0.0},
            "policy_violation_on_not_permissible": {"count": 0, "rate": None},
            "permissible_policy_violation_rate": 0.0,
            "not_permissible_policy_violation_rate": None,
        }

        self.assertTrue(has_permissibility_split_data(metrics))

    def test_compare_without_taxonomy_keeps_policy_violation_default(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root)
            (results_root / "metrics-suite" / "taxonomy.json").unlink()

            result = self.runner.invoke(
                cli,
                [
                    "results",
                    "compare",
                    "metrics-suite",
                    "run-1",
                    "run-2",
                    "--results-dir",
                    str(results_root),
                    "--no-color",
                ],
                terminal_width=180,
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Run Comparison (metrics-suite, Policy violation)", result.output)

    def test_cross_suite_compare_defaults_to_impermissible_split(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            results_root = Path(tmp_dir) / "results"
            _write_split_results(results_root, "metrics-a")
            _write_split_results(results_root, "metrics-b")

            result = self.runner.invoke(
                cli,
                [
                    "results",
                    "compare-suites",
                    "metrics-a/run-1",
                    "metrics-b/run-2",
                    "--results-dir",
                    str(results_root),
                    "--no-color",
                ],
                terminal_width=180,
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Cross-suite comparison (impermissible behavior violated)", result.output)


if __name__ == "__main__":
    unittest.main()
