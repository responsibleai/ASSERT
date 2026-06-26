# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import unittest

from assert_ai.results import compute_prompt_metrics


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


if __name__ == "__main__":
    unittest.main()
