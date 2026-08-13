"""Unit tests for paired McNemar gate decisions."""

from __future__ import annotations

import unittest

from scripts import regression_decision as rd
from scripts import regression_metrics as rm
from scripts.regression_metrics import MetricResult


def _metric(name: str, values: dict[str, int]) -> MetricResult:
    return MetricResult(
        name=name,
        value=sum(values.values()) / len(values) if values else 0.0,
        granularity="per_test_case_binary",
        per_test_case=values,
    )


def _zero_values(n: int) -> dict[str, int]:
    return {f"s{i}": 0 for i in range(n)}


def _build_pair(
    n: int = 100,
) -> tuple[dict[str, MetricResult], dict[str, MetricResult]]:
    baseline = {
        name: _metric(name, _zero_values(n))
        for name in rm.CANONICAL_METRICS
    }
    treatment = {
        name: _metric(name, _zero_values(n))
        for name in rm.CANONICAL_METRICS
    }
    return baseline, treatment


class McNemarTest(unittest.TestCase):
    def test_no_discordants(self) -> None:
        self.assertEqual(rd.mcnemar_one_sided(0, 0, treatment_higher=True), 1.0)

    def test_treatment_higher_strong(self) -> None:
        self.assertLess(
            rd.mcnemar_one_sided(0, 20, treatment_higher=True),
            0.0001,
        )

    def test_treatment_lower_strong(self) -> None:
        self.assertLess(
            rd.mcnemar_one_sided(20, 0, treatment_higher=False),
            0.0001,
        )

    def test_balanced_is_not_significant(self) -> None:
        self.assertGreater(
            rd.mcnemar_one_sided(10, 10, treatment_higher=True),
            0.5,
        )


class ComparePerTestCaseBinaryTest(unittest.TestCase):
    def test_records_discordants_and_common_pairs(self) -> None:
        comparison = rd.compare_per_test_case_binary(
            rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
            _metric(
                rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
                {"s1": 1, "s2": 1, "s3": 0, "baseline-only": 0},
            ),
            _metric(
                rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
                {"s1": 1, "s2": 0, "s3": 1, "treatment-only": 1},
            ),
            direction="lower_is_better",
            alpha=rd.DEFAULT_ALPHA,
            mde=0.05,
        )

        self.assertEqual(comparison.detail["discordant_b"], 1)
        self.assertEqual(comparison.detail["discordant_c"], 1)
        self.assertEqual(comparison.n_pairs, 3)
        self.assertEqual(comparison.baseline_value, 2 / 3)
        self.assertEqual(comparison.treatment_value, 2 / 3)

    def test_p_value_is_always_for_regression_direction(self) -> None:
        baseline = _zero_values(100)
        treatment = {
            test_case_id: int(index < 20)
            for index, test_case_id in enumerate(baseline)
        }

        comparison = rd.compare_per_test_case_binary(
            rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
            _metric(rm.PERMISSIBLE_POLICY_VIOLATION_RATE, baseline),
            _metric(rm.PERMISSIBLE_POLICY_VIOLATION_RATE, treatment),
            direction="lower_is_better",
            alpha=rd.DEFAULT_ALPHA,
            mde=0.05,
        )

        self.assertEqual(comparison.effect, rd.EFFECT_DEGRADED)
        self.assertLess(comparison.p_value, 0.0001)
        self.assertEqual(
            comparison.detail["p_value_hypothesis"],
            "treatment_higher",
        )

    def test_improvement_uses_separate_p_value(self) -> None:
        treatment = _zero_values(100)
        baseline = {
            test_case_id: int(index < 20)
            for index, test_case_id in enumerate(treatment)
        }

        comparison = rd.compare_per_test_case_binary(
            rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
            _metric(rm.PERMISSIBLE_POLICY_VIOLATION_RATE, baseline),
            _metric(rm.PERMISSIBLE_POLICY_VIOLATION_RATE, treatment),
            direction="lower_is_better",
            alpha=rd.DEFAULT_ALPHA,
            mde=0.05,
        )

        self.assertEqual(comparison.effect, rd.EFFECT_IMPROVED)
        self.assertEqual(comparison.p_value, 1.0)
        self.assertLess(comparison.detail["improvement_p_value"], 0.0001)


class DecideTest(unittest.TestCase):
    def test_default_alpha_is_point_one(self) -> None:
        self.assertEqual(rd.DEFAULT_ALPHA, 0.10)

    def test_no_change_passes(self) -> None:
        baseline, treatment = _build_pair()

        report = rd.decide(baseline, treatment, test_set_size=100)

        self.assertEqual(report["decision"]["decision"], rd.DECISION_PASS)
        self.assertEqual(len(report["results"]), 2)

    def test_permissible_policy_violation_increase_blocks(self) -> None:
        baseline, treatment = _build_pair()
        treatment[rm.PERMISSIBLE_POLICY_VIOLATION_RATE] = _metric(
            rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
            {
                test_case_id: int(index < 20)
                for index, test_case_id in enumerate(_zero_values(100))
            },
        )

        report = rd.decide(baseline, treatment, test_set_size=100)

        self.assertEqual(report["decision"]["decision"], rd.DECISION_BLOCK)

    def test_not_permissible_policy_violation_increase_blocks(self) -> None:
        baseline, treatment = _build_pair()
        treatment[rm.NOT_PERMISSIBLE_POLICY_VIOLATION_RATE] = _metric(
            rm.NOT_PERMISSIBLE_POLICY_VIOLATION_RATE,
            {
                test_case_id: int(index < 20)
                for index, test_case_id in enumerate(_zero_values(100))
            },
        )

        report = rd.decide(baseline, treatment, test_set_size=100)

        self.assertEqual(report["decision"]["decision"], rd.DECISION_BLOCK)

    def test_too_few_samples_warns(self) -> None:
        baseline, treatment = _build_pair(n=5)

        report = rd.decide(baseline, treatment, test_set_size=5)

        self.assertEqual(report["decision"]["decision"], rd.DECISION_WARN)
        self.assertTrue(
            all(
                result["effect"] == rd.EFFECT_TOO_FEW
                for result in report["results"]
            )
        )

    def test_p_below_point_one_blocks_at_pr_sample_size(self) -> None:
        baseline, treatment = _build_pair(n=20)
        treatment[rm.PERMISSIBLE_POLICY_VIOLATION_RATE] = _metric(
            rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
            {
                test_case_id: int(index < 4)
                for index, test_case_id in enumerate(_zero_values(20))
            },
        )

        report = rd.decide(
            baseline,
            treatment,
            test_set_size=20,
        )

        self.assertEqual(report["decision"]["decision"], rd.DECISION_BLOCK)
        permissible = next(
            result
            for result in report["results"]
            if result["metric_name"] == rm.PERMISSIBLE_POLICY_VIOLATION_RATE
        )
        self.assertEqual(permissible["effect"], rd.EFFECT_DEGRADED)
        self.assertEqual(permissible["p_value"], 0.0625)

    def test_p_above_point_one_only_warns(self) -> None:
        baseline, treatment = _build_pair(n=20)
        treatment[rm.PERMISSIBLE_POLICY_VIOLATION_RATE] = _metric(
            rm.PERMISSIBLE_POLICY_VIOLATION_RATE,
            {
                test_case_id: int(index < 3)
                for index, test_case_id in enumerate(_zero_values(20))
            },
        )

        report = rd.decide(
            baseline,
            treatment,
            test_set_size=20,
        )

        self.assertEqual(report["decision"]["decision"], rd.DECISION_WARN)
        permissible = next(
            result
            for result in report["results"]
            if result["metric_name"] == rm.PERMISSIBLE_POLICY_VIOLATION_RATE
        )
        self.assertEqual(permissible["effect"], rd.EFFECT_INCONCLUSIVE)
        self.assertEqual(permissible["p_value"], 0.125)


if __name__ == "__main__":
    unittest.main()
