# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for token-usage reporting in the runner.

Covers the helpers that surface ``UsageAccumulator`` data on stage completion
lines and aggregate it into ``metrics.json`` at the end of a pipeline run.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assert_ai.core.model_client import UsageAccumulator, UsageStats
from assert_ai.runner import (
    _MetricsFormatError,
    _build_run_metrics,
    _format_token_count,
    _format_usage_line,
    _log_token_estimate,
    _read_existing_run_metrics,
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
    def test_zero_token_estimate_logs_opaque_target_caveat(self) -> None:
        note = "Target-internal usage for the callable target is not included."
        with self.assertLogs("assert_ai.runner", level="INFO") as captured:
            _log_token_estimate({"total_tokens": 0, "calls": 0, "notes": [note]})
        text = "\n".join(captured.output)
        self.assertIn("0 tracked calls", text)
        self.assertIn(note, text)

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

    def test_renders_total_only_usage(self) -> None:
        usage = UsageAccumulator(
            requests=1,
            calls=1,
            total_tokens=123,
        )
        self.assertIn("123 total", _format_usage_line(usage))

    def test_renders_mixed_detailed_and_total_only_usage(self) -> None:
        usage = UsageAccumulator()
        usage.add(
            UsageStats(prompt_tokens=100, completion_tokens=20),
            model="detailed",
        )
        usage.add(UsageStats(total_tokens=500), model="total-only")

        line = _format_usage_line(usage)
        self.assertIn("2 calls", line)
        self.assertIn("100 in / 20 out / 620 total", line)


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
        self.assertEqual(payload["stages"]["judge"]["calls"], 100)
        self.assertEqual(payload["stages"]["test_set"]["calls"], 15)
        totals = payload["totals"]
        self.assertEqual(totals["calls"], 115)
        self.assertEqual(totals["input_tokens"], 875_000)
        self.assertEqual(totals["output_tokens"], 17_000)
        self.assertEqual(totals["total_tokens"], 892_000)
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

    def test_records_estimate_and_actual_error(self) -> None:
        stage_usage = {
            "judge": {
                "calls": 2,
                "input_tokens": 800,
                "output_tokens": 200,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "per_model": {},
            },
        }
        estimate = {
            "total_tokens": 1_100,
            "input_tokens": 900,
            "output_tokens": 200,
            "calls": 2,
            "stages": {
                "judge": {
                    "calls": 2,
                    "total_tokens": 1_100,
                },
            },
        }

        payload = _build_run_metrics(
            stage_usage,
            total_elapsed=1.0,
            token_estimate=estimate,
        )

        self.assertEqual(payload["token_estimate"], estimate)
        accuracy = payload["token_estimate_accuracy"]
        self.assertEqual(accuracy["status"], "available")
        self.assertEqual(accuracy["scope"], "current_invocation")
        self.assertEqual(accuracy["actual_total_tokens"], 1_000)
        self.assertEqual(accuracy["difference_tokens"], -100)
        self.assertAlmostEqual(accuracy["difference_ratio"], -100 / 1_100)
        self.assertAlmostEqual(
            accuracy["absolute_percentage_error"],
            100 / 1_100,
        )

    def test_marks_accuracy_unavailable_when_usage_is_incomplete(self) -> None:
        stage_usage = {
            "judge": {
                "requests": 2,
                "calls": 1,
                "missing_usage_calls": 1,
                "input_tokens": 800,
                "output_tokens": 200,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "per_model": {},
            },
        }

        payload = _build_run_metrics(
            stage_usage,
            total_elapsed=1.0,
            token_estimate={
                "total_tokens": 1_100,
                "stages": {"judge": {"calls": 2, "total_tokens": 1_100}},
            },
        )

        accuracy = payload["token_estimate_accuracy"]
        self.assertEqual(accuracy["status"], "unavailable")
        self.assertEqual(accuracy["reason"], "provider_usage_incomplete")
        self.assertEqual(accuracy["usage_coverage"], 0.5)

    def test_marks_accuracy_unavailable_when_pipeline_fails(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "input_tokens": 800,
                    "output_tokens": 200,
                    "per_model": {},
                },
            },
            total_elapsed=1.0,
            token_estimate={
                "total_tokens": 1_100,
                "stages": {"judge": {"calls": 1, "total_tokens": 1_100}},
            },
            run_completed=False,
        )

        accuracy = payload["token_estimate_accuracy"]
        self.assertEqual(accuracy["status"], "unavailable")
        self.assertEqual(accuracy["reason"], "pipeline_incomplete")

    def test_marks_accuracy_unavailable_when_pipeline_is_partial(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "total_tokens": 1_000,
                    "input_tokens": 800,
                    "output_tokens": 200,
                    "per_model": {},
                },
            },
            total_elapsed=1.0,
            token_estimate={"total_tokens": 1_100},
            run_partial=True,
        )

        accuracy = payload["token_estimate_accuracy"]
        self.assertEqual(accuracy["status"], "unavailable")
        self.assertEqual(accuracy["reason"], "pipeline_partial")

    def test_accuracy_uses_provider_total_when_breakdown_is_missing(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "total_tokens": 1_000,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "per_model": {},
                },
            },
            total_elapsed=1.0,
            token_estimate={
                "total_tokens": 1_100,
                "stages": {"judge": {"calls": 1, "total_tokens": 1_100}},
            },
        )

        accuracy = payload["token_estimate_accuracy"]
        self.assertEqual(accuracy["status"], "available")
        self.assertEqual(accuracy["actual_total_tokens"], 1_000)

    def test_force_stage_with_no_tracked_usage_removes_stale_stage_usage(self) -> None:
        payload = _build_run_metrics(
            {},
            total_elapsed=1.0,
            existing_metrics={
                "stages": {
                    "inference": {"calls": 2, "total_tokens": 1_000},
                    "judge": {"calls": 1, "total_tokens": 500},
                },
            },
            stage_merge_modes={"judge": "replace"},
        )

        self.assertNotIn("judge", payload["stages"])
        self.assertEqual(payload["totals"]["total_tokens"], 1_000)

    def test_accuracy_compares_estimate_with_current_invocation_only(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "input_tokens": 80,
                    "output_tokens": 20,
                },
            },
            total_elapsed=1.0,
            token_estimate={
                "total_tokens": 110,
                "stages": {"judge": {"calls": 1, "total_tokens": 110}},
            },
            existing_metrics={
                "stages": {
                    "inference": {
                        "requests": 10,
                        "calls": 10,
                        "total_tokens": 10_000,
                    },
                },
            },
            stage_merge_modes={"judge": "accumulate"},
        )

        accuracy = payload["token_estimate_accuracy"]
        self.assertEqual(payload["totals"]["total_tokens"], 10_100)
        self.assertEqual(accuracy["actual_total_tokens"], 100)
        self.assertEqual(accuracy["estimated_total_tokens"], 110)
        self.assertEqual(accuracy["scope"], "current_invocation")

    def test_accuracy_is_unavailable_when_stage_scopes_differ(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "total_tokens": 100,
                },
            },
            total_elapsed=1.0,
            token_estimate={
                "total_tokens": 110,
                "stages": {"inference": {"calls": 1, "total_tokens": 110}},
            },
        )

        self.assertEqual(
            payload["token_estimate_accuracy"],
            {
                "status": "unavailable",
                "reason": "stage_scope_mismatch",
                "scope": "current_invocation",
                "estimated_stages": ["inference"],
                "actual_stages": ["judge"],
            },
        )

    def test_accuracy_is_unavailable_when_estimate_has_no_usage(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "total_tokens": 100,
                },
            },
            total_elapsed=1.0,
            token_estimate={"total_tokens": 0, "stages": {}},
        )

        self.assertEqual(
            payload["token_estimate_accuracy"],
            {
                "status": "unavailable",
                "reason": "no_estimated_usage",
                "scope": "current_invocation",
            },
        )

    def test_prior_estimate_is_not_compared_with_new_actual_usage(self) -> None:
        payload = _build_run_metrics(
            {
                "judge": {
                    "requests": 1,
                    "calls": 1,
                    "total_tokens": 100,
                },
            },
            total_elapsed=1.0,
            existing_metrics={
                "stages": {},
                "token_estimate": {"total_tokens": 1_000},
            },
        )

        self.assertEqual(
            payload["token_estimate_accuracy"],
            {
                "status": "unavailable",
                "reason": "estimate_scope_mismatch",
                "scope": "current_invocation",
            },
        )
        self.assertEqual(payload["token_estimate_scope"], "prior_invocation")


class ExistingMetricsCompatibilityTest(unittest.TestCase):
    def test_loads_old_token_metrics_with_missing_derived_fields(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            metrics_path = Path(tmp_dir) / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "stages": {
                            "judge": {
                                "calls": 1,
                                "input_tokens": 80,
                                "output_tokens": 20,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = _read_existing_run_metrics(metrics_path)

        assert payload is not None
        self.assertEqual(payload["stages"]["judge"]["requests"], 1)
        self.assertEqual(payload["stages"]["judge"]["total_tokens"], 100)
        self.assertEqual(payload["stages"]["judge"]["per_model"], {})

    def test_legacy_non_token_metrics_are_preserved(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            metrics_path = Path(tmp_dir) / "metrics.json"
            metrics_path.write_text(
                json.dumps({"scenario_metrics": {"total": 3}}),
                encoding="utf-8",
            )

            with self.assertLogs("assert_ai.runner", level="WARNING"):
                existing = _read_existing_run_metrics(metrics_path)
            payload = _build_run_metrics(
                {"judge": {"calls": 1, "total_tokens": 100}},
                total_elapsed=1.0,
                existing_metrics=existing,
            )

        self.assertEqual(payload["scenario_metrics"], {"total": 3})
        self.assertEqual(payload["totals"]["total_tokens"], 100)

    def test_rejects_malformed_metrics_instead_of_silently_overwriting(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            metrics_path = Path(tmp_dir) / "metrics.json"
            metrics_path.write_text('{"stages": []}', encoding="utf-8")

            with self.assertRaisesRegex(
                _MetricsFormatError,
                "field 'stages' must be an object",
            ):
                _read_existing_run_metrics(metrics_path)


if __name__ == "__main__":
    unittest.main()
