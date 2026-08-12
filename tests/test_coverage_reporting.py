"""Tests for coverage reporting on judged rows.

Rows that could not be judged are dropped from every rate denominator. The
exclusion is directional, not random: a provider content filter rejects the most
adversarial transcripts, which are the ones most likely to contain a real
violation. Reporting the rate without the denominator therefore biases it
optimistically.
"""

from __future__ import annotations

from assert_ai.results import (
    COVERAGE_WARN_THRESHOLD,
    compute_coverage,
    format_coverage,
)


def _ok_row(**extra):
    row = {
        "judge_status": "ok",
        "verdict": {
            "dimensions": {"policy_violation": False, "overrefusal": False},
            "node_judgments": [
                {"node_name": "b1", "violated": False, "confidence": "high"}
            ],
        },
        "score_keys": ["policy_violation", "overrefusal"],
    }
    row.update(extra)
    return row


def _excluded_row(status: str):
    return {
        "judge_status": status,
        "verdict": {"error": status},
        "score_keys": ["policy_violation", "overrefusal"],
    }


class TestComputeCoverage:
    def test_all_scored(self):
        cov = compute_coverage([_ok_row(), _ok_row()])
        assert cov["total"] == 2
        assert cov["scored"] == 2
        assert cov["excluded"] == 0
        assert cov["scored_rate"] == 1.0
        assert cov["below_threshold"] is False

    def test_empty_rows(self):
        cov = compute_coverage([])
        assert cov["total"] == 0
        assert cov["scored"] == 0
        assert cov["scored_rate"] == 0.0
        assert cov["below_threshold"] is False

    def test_denominator_excludes_unjudged_rows(self):
        rows = [_ok_row() for _ in range(7)] + [
            _excluded_row("filter_skipped") for _ in range(3)
        ]
        cov = compute_coverage(rows)
        assert cov["total"] == 10
        assert cov["scored"] == 7
        assert cov["excluded"] == 3
        assert cov["scored_rate"] == 0.7

    def test_filter_skipped_is_distinguished_from_judge_failure(self):
        """infer_judge_status flattens both to judge_failed; the breakdown must not."""
        rows = [
            _ok_row(),
            _excluded_row("filter_skipped"),
            _excluded_row("judge_failed"),
        ]
        cov = compute_coverage(rows)
        assert cov["by_status"]["filter_skipped"] == 1
        assert cov["by_status"]["judge_failed"] == 1
        assert cov["by_status"]["ok"] == 1

    def test_threshold_not_tripped_just_below(self):
        rows = [_ok_row() for _ in range(91)] + [_excluded_row("judge_failed") for _ in range(9)]
        cov = compute_coverage(rows)
        assert cov["excluded_rate"] < COVERAGE_WARN_THRESHOLD
        assert cov["below_threshold"] is False

    def test_threshold_tripped_just_above(self):
        rows = [_ok_row() for _ in range(89)] + [_excluded_row("judge_failed") for _ in range(11)]
        cov = compute_coverage(rows)
        assert cov["excluded_rate"] > COVERAGE_WARN_THRESHOLD
        assert cov["below_threshold"] is True

    def test_row_claiming_ok_without_a_verdict_is_not_counted_as_scored(self):
        rows = [{"judge_status": "ok", "verdict": {}, "score_keys": ["policy_violation"]}]
        cov = compute_coverage(rows)
        assert cov["scored"] == 0
        assert cov["by_status"].get("judge_failed") == 1

class TestFormatCoverage:
    def test_reports_scored_over_total(self):
        line = format_coverage(compute_coverage([_ok_row(), _excluded_row("judge_failed")]))
        assert "1/2" in line
        assert "50.0%" in line

    def test_lists_excluded_statuses(self):
        rows = [_ok_row()] + [_excluded_row("filter_skipped") for _ in range(2)]
        line = format_coverage(compute_coverage(rows))
        assert "2 filter_skipped" in line

    def test_clean_run_has_no_exclusion_detail(self):
        line = format_coverage(compute_coverage([_ok_row()]))
        assert "filter_skipped" not in line
        assert "judge_failed" not in line


class TestMetricsIncludeCoverage:
    def test_coverage_present_in_computed_metrics(self):
        from assert_ai.results import compute_prompt_metrics

        rows = [_ok_row() for _ in range(3)] + [_excluded_row("filter_skipped")]
        metrics = compute_prompt_metrics(rows)
        assert metrics is not None
        assert metrics["coverage"]["total"] == 4
        assert metrics["coverage"]["scored"] == 3

    def test_low_coverage_logs_a_warning(self, caplog):
        from assert_ai.results import compute_prompt_metrics

        rows = [_ok_row() for _ in range(5)] + [
            _excluded_row("filter_skipped") for _ in range(5)
        ]
        with caplog.at_level("WARNING"):
            compute_prompt_metrics(rows)
        assert "true rate is likely higher" in caplog.text
