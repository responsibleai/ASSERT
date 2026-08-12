# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import unittest

from assert_ai.cli import (
    _has_permissibility_split,
    _violation_cells,
    _violation_column_titles,
)
from assert_ai.results import (
    compute_policy_violation_by_permissibility,
    compute_prompt_metrics,
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
            ),
        )
        self.assertEqual(_violation_cells(metrics, metrics, True), ("-", "50.0%", "-"))

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


if __name__ == "__main__":
    unittest.main()
