from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("summarize_run_prefixes.py")
SPEC = importlib.util.spec_from_file_location("summarize_run_prefixes", MODULE_PATH)
assert SPEC and SPEC.loader
summary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = summary
SPEC.loader.exec_module(summary)


class FinalTaxonomyProjectionTests(unittest.TestCase):
    def test_counts_groups_when_their_first_member_appears(self) -> None:
        rows = [
            {"run_count": 1, "global_unique_dimensions": 4, "relevant_unique_dimensions": 4},
            {"run_count": 2, "global_unique_dimensions": 3, "relevant_unique_dimensions": 3},
            {"run_count": 3, "global_unique_dimensions": 4, "relevant_unique_dimensions": 3},
        ]
        final_metrics = {
            "global_unique_metrics": {
                "example_harm": {
                    "unique_dimension_count": 4,
                    "relevant_unique_dimension_count": 3,
                    "unique_groups": [
                        {
                            "member_dimension_ids": ["example_harm/run-1/1"],
                            "is_relevant_at_binary_threshold": True,
                        },
                        {
                            "member_dimension_ids": ["example_harm/run-2/1"],
                            "is_relevant_at_binary_threshold": True,
                        },
                        {
                            "member_dimension_ids": ["example_harm/run-3/1"],
                            "is_relevant_at_binary_threshold": False,
                        },
                        {
                            "member_dimension_ids": [
                                "example_harm/run-1/2",
                                "example_harm/run-3/2",
                            ],
                            "is_relevant_at_binary_threshold": True,
                        },
                    ],
                }
            }
        }

        summary.apply_final_taxonomy_counts(
            rows,
            final_metrics,
            "example_harm",
            "example_harm/runs-1-to-3/metrics.json",
        )

        self.assertEqual(
            [row["global_unique_dimensions"] for row in rows], [2, 3, 4]
        )
        self.assertEqual(
            [row["relevant_unique_dimensions"] for row in rows], [2, 3, 3]
        )
        self.assertEqual(
            {row["unique_taxonomy_metrics"] for row in rows},
            {"example_harm/runs-1-to-3/metrics.json"},
        )


class CumulativeCoverageTests(unittest.TestCase):
    def test_preserves_raw_scores_and_removes_decreases(self) -> None:
        rows = [
            {"run_count": 3, "coverage_score": 80},
            {"run_count": 5, "coverage_score": 89},
            {"run_count": 7, "coverage_score": 92},
            {"run_count": 8, "coverage_score": 86},
        ]

        summary.apply_cumulative_coverage(rows, "child_safety")

        self.assertEqual(
            [row["raw_coverage_score"] for row in rows], [80, 89, 92, 86]
        )
        self.assertEqual(
            [row["coverage_score"] for row in rows], [80, 89, 92, 92]
        )


if __name__ == "__main__":
    unittest.main()