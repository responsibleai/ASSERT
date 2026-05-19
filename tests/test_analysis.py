"""Tests for p2m.analysis modules — stats, rollout_metrics, stability, suite_analysis."""

import json
import tempfile
from pathlib import Path

import pytest

from p2m.analysis.stats import binary_rate_ci, macro_rate, _wilson_ci
from p2m.analysis.rollout_metrics import count_rollout_turns, compute_rollout_metrics
from p2m.analysis.stability import (
    compute_auditor_variation,
    compute_repeatability,
)


# --- stats.py ---


class TestWilsonCI:
    def test_zero_of_n_gives_nonzero_upper(self):
        lo, hi = _wilson_ci(0, 50, alpha=0.10)
        assert lo == 0.0
        assert hi > 0.0

    def test_all_positive_gives_nonone_lower(self):
        lo, hi = _wilson_ci(50, 50, alpha=0.10)
        assert lo < 1.0
        assert hi == pytest.approx(1.0)

    def test_empty_sample(self):
        lo, hi = _wilson_ci(0, 0)
        assert lo == 0.0
        assert hi == 0.0

    def test_half_rate(self):
        lo, hi = _wilson_ci(25, 50, alpha=0.10)
        assert 0.3 < lo < 0.5
        assert 0.5 < hi < 0.7


class TestBinaryRateCI:
    def test_empty_returns_none(self):
        result = binary_rate_ci([])
        assert result["rate"] is None
        assert result["n"] == 0

    def test_single_run_uses_wilson(self):
        outcomes = [True] * 7 + [False] * 3
        result = binary_rate_ci(outcomes)
        assert result["rate"] == pytest.approx(0.7)
        assert result["ci_lower"] < 0.7
        assert result["ci_upper"] > 0.7
        assert result["n"] == 10

    def test_cluster_size_one_uses_wilson(self):
        outcomes = [True, False, True]
        groups = ["a", "b", "c"]
        result = binary_rate_ci(outcomes, groups=groups)
        assert result["ci_lower"] is not None

    def test_all_false_gives_zero_rate_with_nonzero_upper(self):
        outcomes = [False] * 50
        result = binary_rate_ci(outcomes)
        assert result["rate"] == 0.0
        assert result["ci_lower"] == 0.0
        assert result["ci_upper"] > 0.0


class TestMacroRate:
    def test_equal_groups(self):
        groups = {
            "a": {"count": 10, "rate": 0.5},
            "b": {"count": 10, "rate": 0.8},
        }
        result = macro_rate(groups)
        assert result["rate"] == pytest.approx(0.65)
        assert result["n_groups_included"] == 2

    def test_min_support_excludes_small_groups(self):
        groups = {
            "a": {"count": 10, "rate": 0.5},
            "b": {"count": 2, "rate": 1.0},
        }
        result = macro_rate(groups, min_support=5)
        assert result["rate"] == pytest.approx(0.5)
        assert result["n_groups_included"] == 1
        assert "b" in result["groups_excluded"]

    def test_all_excluded_returns_none(self):
        groups = {"a": {"count": 1, "rate": 0.5}}
        result = macro_rate(groups, min_support=5)
        assert result["rate"] is None


# --- rollout_metrics.py ---


def _make_transcript(stop_reason="max_turns", n_auditor_turns=3):
    events = []
    for _ in range(n_auditor_turns):
        events.append({
            "view": ["target"],
            "actor": "auditor",
            "edit": {"message": {"role": "user", "content": "hello"}},
        })
        events.append({
            "view": ["auditor"],
            "actor": "target",
            "edit": {"message": {"role": "assistant", "content": "hi"}},
        })
    return {"stop_reason": stop_reason, "events": events, "dimensions": {"behavior": "test_behavior"}}


class TestCountRolloutTurns:
    def test_counts_auditor_user_messages_to_target(self):
        row = _make_transcript(n_auditor_turns=5)
        assert count_rollout_turns(row) == 5

    def test_empty_events(self):
        assert count_rollout_turns({"events": []}) == 0

    def test_missing_events(self):
        assert count_rollout_turns({}) == 0


class TestComputeRolloutMetrics:
    def test_empty_returns_zero_total(self):
        assert compute_rollout_metrics([])["total"] == 0

    def test_all_completed(self):
        rows = [_make_transcript("max_turns") for _ in range(10)]
        result = compute_rollout_metrics(rows)
        assert result["total"] == 10
        assert result["completion_rate"] == 1.0
        assert result["error_rate"] == 0.0

    def test_mixed_stop_reasons(self):
        rows = [
            _make_transcript("max_turns"),
            _make_transcript("max_turns"),
            _make_transcript("target_error"),
            _make_transcript("invalid_auditor_turn"),
        ]
        result = compute_rollout_metrics(rows)
        assert result["completion_rate"] == 0.5
        assert result["error_rate"] == 0.25
        assert result["invalid_auditor_rate"] == 0.25


# --- stability.py ---


def _make_scored_rows(seed_outcomes: dict[str, list[bool]], runs: list[str]):
    """Build scored rows from a seed→per-run-outcomes mapping."""
    rows = []
    for sid, outcomes in seed_outcomes.items():
        for run, outcome in zip(runs, outcomes):
            rows.append({"test_case_id": sid, "run": run, "policy_violation": outcome})
    return rows


class TestComputeRepeatability:
    def test_perfect_agreement(self):
        rows = _make_scored_rows(
            {"s1": [True, True], "s2": [False, False]},
            ["run1", "run2"],
        )
        result = compute_repeatability(rows)
        assert result["agreement_rate"] == 1.0
        assert result["n_always_violate"] == 1
        assert result["n_always_clear"] == 1
        assert result["n_mixed"] == 0

    def test_no_agreement(self):
        rows = _make_scored_rows(
            {"s1": [True, False], "s2": [False, True]},
            ["run1", "run2"],
        )
        result = compute_repeatability(rows)
        assert result["agreement_rate"] == 0.0
        assert result["n_mixed"] == 2

    def test_empty_returns_zero(self):
        result = compute_repeatability([])
        assert result["n_seeds"] == 0


class TestComputeAuditorVariation:
    def test_two_auditors(self):
        rows = [
            {"test_case_id": "s1", "auditor_model": "a1", "policy_violation": True},
            {"test_case_id": "s2", "auditor_model": "a1", "policy_violation": False},
            {"test_case_id": "s1", "auditor_model": "a2", "policy_violation": False},
            {"test_case_id": "s2", "auditor_model": "a2", "policy_violation": False},
        ]
        result = compute_auditor_variation(rows)
        assert result["n_auditors"] == 2
        assert result["rate_range"] > 0


# --- suite_analysis.py (integration) ---


def _make_suite_dir(tmp_path: Path):
    """Create a minimal suite directory with one run."""
    suite = tmp_path / "test-suite"
    suite.mkdir()
    run = suite / "run1"
    run.mkdir()

    transcripts = [
        {"test_case_id": f"s{i}", "stop_reason": "max_turns", "dimensions": {"behavior": "b1"},
         "events": [{"view": ["target"], "actor": "auditor",
                     "edit": {"message": {"role": "user", "content": "hi"}}}] * 3}
        for i in range(10)
    ]
    scores = [
        {"test_case_id": f"s{i}", "judge_status": "ok", "dimensions": {"behavior": "b1"},
         "verdict": {"dimensions": {"policy_violation": i < 4, "overrefusal": False}}}
        for i in range(10)
    ]
    (run / "transcripts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in transcripts))
    (run / "scores.jsonl").write_text(
        "\n".join(json.dumps(r) for r in scores))
    return suite


class TestAnalyzeSuite:
    def test_runs_on_minimal_suite(self, tmp_path):
        from p2m.analysis.suite_analysis import analyze_suite, format_suite_summary
        suite = _make_suite_dir(tmp_path)
        results = analyze_suite(str(suite), n_boot=100)
        assert "runs" in results
        assert "run1" in results["runs"]
        output = format_suite_summary(results)
        assert "Taxonomy violations" in output

    def test_empty_suite_returns_error(self, tmp_path):
        from p2m.analysis.suite_analysis import analyze_suite
        empty = tmp_path / "empty"
        empty.mkdir()
        result = analyze_suite(str(empty))
        assert "error" in result
