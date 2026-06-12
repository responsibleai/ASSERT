# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for token-usage reporting in the runner.

Covers the helpers that surface ``UsageAccumulator`` data on stage completion
lines and aggregate it into ``metrics.json`` at the end of a pipeline run.
"""

import unittest

from assert_ai.core.model_client import UsageAccumulator, UsageStats
from assert_ai.runner import (
    _build_run_metrics,
    _format_token_count,
    _format_usage_line,
)


class FormatTokenCountTest(unittest.TestCase):
    def test_units_below_one_thousand_are_raw(self) -> None:
        self.assertEqual(_format_token_count(0), "0")
        self.assertEqual(_format_token_count(42), "42")
        self.assertEqual(_format_token_count(999), "999")

    def test_thousands_render_with_one_decimal(self) -> None:
        self.assertEqual(_format_token_count(1_000), "1.0K")
        self.assertEqual(_format_token_count(12_500), "12.5K")
        self.assertEqual(_format_token_count(999_999), "1000.0K")

    def test_millions_render_with_one_decimal(self) -> None:
        self.assertEqual(_format_token_count(1_000_000), "1.0M")
        self.assertEqual(_format_token_count(2_750_000), "2.8M")


class FormatUsageLineTest(unittest.TestCase):
    def test_returns_empty_when_no_calls(self) -> None:
        self.assertEqual(_format_usage_line(None), "")
        self.assertEqual(_format_usage_line(UsageAccumulator()), "")

    def test_renders_calls_tokens_and_cache_pct(self) -> None:
        usage = UsageAccumulator()
        usage.add(
            UsageStats(
                prompt_tokens=10_000,
                completion_tokens=2_000,
                cached_input_tokens=2_500,
            ),
            model="azure/gpt-5.4-mini",
        )
        line = _format_usage_line(usage)
        self.assertIn("1 call", line)
        self.assertIn("10.0K in", line)
        self.assertIn("2.0K out", line)
        self.assertIn("25.0% cached", line)
        self.assertTrue(line.startswith(" | "))

    def test_pluralizes_call_count(self) -> None:
        usage = UsageAccumulator()
        usage.add(UsageStats(prompt_tokens=100, completion_tokens=10), model="m")
        usage.add(UsageStats(prompt_tokens=100, completion_tokens=10), model="m")
        self.assertIn("2 calls", _format_usage_line(usage))

    def test_omits_cache_percentage_when_no_input_tokens(self) -> None:
        # An accumulator can have calls=0 input_tokens=0 if every response
        # arrived without usage metadata. _format_usage_line skips the percent.
        usage = UsageAccumulator(calls=1, input_tokens=0, output_tokens=5)
        self.assertNotIn("cached", _format_usage_line(usage))


class BuildRunMetricsTest(unittest.TestCase):
    def test_aggregates_per_stage_into_totals(self) -> None:
        stage_usage = {
            "test_set": {
                "calls": 15,
                "input_tokens": 75_000,
                "output_tokens": 5_000,
                "cached_input_tokens": 30_000,
                "cache_creation_input_tokens": 0,
                "elapsed_s": 41.3,
                "per_model": {
                    "azure/gpt-5.4-mini": {
                        "calls": 15,
                        "input_tokens": 75_000,
                        "output_tokens": 5_000,
                        "cached_input_tokens": 30_000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
            "judge": {
                "calls": 100,
                "input_tokens": 800_000,
                "output_tokens": 12_000,
                "cached_input_tokens": 600_000,
                "cache_creation_input_tokens": 0,
                "elapsed_s": 112.0,
                "per_model": {
                    "azure/gpt-5.4-mini": {
                        "calls": 100,
                        "input_tokens": 800_000,
                        "output_tokens": 12_000,
                        "cached_input_tokens": 600_000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        }
        payload = _build_run_metrics(stage_usage, total_elapsed=200.5)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["elapsed_s"], 200.5)
        self.assertEqual(payload["stages"], stage_usage)
        totals = payload["totals"]
        self.assertEqual(totals["calls"], 115)
        self.assertEqual(totals["input_tokens"], 875_000)
        self.assertEqual(totals["output_tokens"], 17_000)
        self.assertEqual(totals["cached_input_tokens"], 630_000)
        self.assertAlmostEqual(totals["cache_hit_rate"], 630_000 / 875_000)
        per_model = payload["per_model"]["azure/gpt-5.4-mini"]
        self.assertEqual(per_model["calls"], 115)
        self.assertEqual(per_model["input_tokens"], 875_000)

    def test_handles_empty_stage_usage(self) -> None:
        payload = _build_run_metrics({}, total_elapsed=0.5)
        self.assertEqual(payload["totals"]["calls"], 0)
        self.assertEqual(payload["totals"]["cache_hit_rate"], 0.0)
        self.assertEqual(payload["per_model"], {})


if __name__ == "__main__":
    unittest.main()
