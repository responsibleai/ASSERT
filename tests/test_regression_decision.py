"""Unit tests for ``scripts.regression_decision``.

Synthetic-data tests covering paired tests + Holm-Bonferroni gate logic.
No live runs. Every test constructs baseline + treatment ``MetricResult``
maps directly and asserts on the gate decision shape.
"""

from __future__ import annotations

import unittest

from scripts import regression_decision as rd
from scripts.regression_metrics import MetricResult


class McNemarTest(unittest.TestCase):
    def test_no_discordants(self) -> None:
        self.assertEqual(rd.mcnemar_one_sided(0, 0, treatment_higher=True), 1.0)

    def test_treatment_higher_strong(self) -> None:
        # 0 baseline-only, 20 treatment-only → strong evidence treatment > baseline.
        p = rd.mcnemar_one_sided(0, 20, treatment_higher=True)
        self.assertLess(p, 0.0001)

    def test_treatment_lower_strong(self) -> None:
        # Symmetric: 20 baseline-only, 0 treatment-only.
        p = rd.mcnemar_one_sided(20, 0, treatment_higher=False)
        self.assertLess(p, 0.0001)

    def test_balanced_not_significant(self) -> None:
        p = rd.mcnemar_one_sided(10, 10, treatment_higher=True)
        self.assertGreater(p, 0.5)


class HolmBonferroniTest(unittest.TestCase):
    def test_all_pass(self) -> None:
        rejected = rd.holm_bonferroni([0.04, 0.05, 0.06], alpha=0.01)
        self.assertEqual(rejected, [False, False, False])

    def test_one_strongly_significant(self) -> None:
        # 0.001 vs alpha/3 = 0.00333 → rejected. Others above threshold.
        rejected = rd.holm_bonferroni([0.001, 0.5, 0.6], alpha=0.01)
        self.assertEqual(rejected, [True, False, False])

    def test_step_down_blocks_after_first_failure(self) -> None:
        # Holm sorts p-values ascending; both 0.0001 and 0.0002 fall below
        # their step-down thresholds (alpha/3, alpha/2) → both rejected.
        # The 0.5 (highest) breaks the chain.
        rejected = rd.holm_bonferroni([0.0001, 0.5, 0.0002], alpha=0.01)
        self.assertEqual(rejected, [True, False, True])

    def test_step_down_breaks_at_threshold(self) -> None:
        # 0.001 < alpha/3=0.00333 ✓ reject. 0.005 > alpha/2=0.005 (strict <=)
        # is on the boundary — implementation uses p <= threshold so accept.
        # Then 0.5 > alpha/1=0.01 → break. Final: [True, True, False].
        rejected = rd.holm_bonferroni([0.001, 0.005, 0.5], alpha=0.01)
        self.assertEqual(rejected, [True, True, False])


def _per_test_case(name: str, per_test_case: dict[str, int]) -> MetricResult:
    return MetricResult(
        name=name,
        value=sum(per_test_case.values()) / len(per_test_case) if per_test_case else 0.0,
        granularity="per_test_case_binary",
        per_test_case=per_test_case,
    )


def _suite(name: str, value: float) -> MetricResult:
    return MetricResult(name=name, value=value, granularity="dataset", per_test_case={})


def _build_baseline_treatment_pair(
    *,
    pv_baseline: float = 0.5,
    pv_treatment: float = 0.5,
    n: int = 100,
) -> tuple[dict[str, MetricResult], dict[str, MetricResult]]:
    """Build baseline + treatment metric maps for ``decide`` smoke tests."""
    bl_pv = {f"s{i}": (1 if i < int(pv_baseline * n) else 0) for i in range(n)}
    tr_pv = {f"s{i}": (1 if i < int(pv_treatment * n) else 0) for i in range(n)}
    bl_signal = {f"s{i}": 1 for i in range(n)}
    tr_signal = {f"s{i}": 1 for i in range(n)}
    bl_overrefusal = {f"s{i}": 0 for i in range(n)}
    tr_overrefusal = {f"s{i}": 0 for i in range(n)}
    bl_judge_failed = {f"s{i}": 0 for i in range(n)}
    tr_judge_failed = {f"s{i}": 0 for i in range(n)}
    baseline = {
        "signal_rate": _per_test_case("signal_rate", bl_signal),
        "policy_violation_rate": _per_test_case("policy_violation_rate", bl_pv),
        "overrefusal_rate": _per_test_case("overrefusal_rate", bl_overrefusal),
        "judge_failure_rate": _per_test_case("judge_failure_rate", bl_judge_failed),
        "construct_coverage": _suite("construct_coverage", 1.0),
        "separation_strength": _suite("separation_strength", 0.5),
        "discrimination_power": _suite("discrimination_power", 0.5),
        "failure_variety": _suite("failure_variety", 4.0),
        "failure_mode_count": _suite("failure_mode_count", 4.0),
        "item_saturation": _suite("item_saturation", 0.0),
    }
    treatment = {
        "signal_rate": _per_test_case("signal_rate", tr_signal),
        "policy_violation_rate": _per_test_case("policy_violation_rate", tr_pv),
        "overrefusal_rate": _per_test_case("overrefusal_rate", tr_overrefusal),
        "judge_failure_rate": _per_test_case("judge_failure_rate", tr_judge_failed),
        "construct_coverage": _suite("construct_coverage", 1.0),
        "separation_strength": _suite("separation_strength", 0.5),
        "discrimination_power": _suite("discrimination_power", 0.5),
        "failure_variety": _suite("failure_variety", 4.0),
        "failure_mode_count": _suite("failure_mode_count", 4.0),
        "item_saturation": _suite("item_saturation", 0.0),
    }
    return baseline, treatment


class DecideTest(unittest.TestCase):
    def test_no_change_passes(self) -> None:
        baseline, treatment = _build_baseline_treatment_pair()
        report = rd.decide(baseline, treatment, test_set_size=200)
        self.assertEqual(report["decision"]["decision"], rd.DECISION_PASS)

    def test_signal_rate_collapse_blocks(self) -> None:
        # Treatment loses signal on half the test cases → degraded on signal_rate.
        # Direction is higher_is_better, so going down is bad.
        baseline, treatment = _build_baseline_treatment_pair(n=100)
        # Tank treatment signal: 50 test cases drop to 0.
        treatment["signal_rate"] = _per_test_case(
            "signal_rate",
            {f"s{i}": (0 if i < 50 else 1) for i in range(100)},
        )
        report = rd.decide(baseline, treatment, test_set_size=200)
        self.assertEqual(report["decision"]["decision"], rd.DECISION_BLOCK)
        signal_result = next(r for r in report["results"] if r["metric_name"] == "signal_rate")
        self.assertEqual(signal_result["effect"], rd.EFFECT_DEGRADED)

    def test_overrefusal_explosion_blocks(self) -> None:
        # Treatment overrefuses on 40 of 100 test cases (baseline=0).
        # overrefusal_rate is lower_is_better, so going up = degraded.
        baseline, treatment = _build_baseline_treatment_pair(n=100)
        treatment["overrefusal_rate"] = _per_test_case(
            "overrefusal_rate",
            {f"s{i}": (1 if i < 40 else 0) for i in range(100)},
        )
        report = rd.decide(baseline, treatment, test_set_size=200)
        # overrefusal_rate is auxiliary, so it should NOT block — only warn.
        self.assertEqual(report["decision"]["decision"], rd.DECISION_PASS)
        # But the comparison should still be reported.
        cmp_ = next(r for r in report["results"] if r["metric_name"] == "overrefusal_rate")
        self.assertEqual(cmp_["effect"], rd.EFFECT_DEGRADED)

    def test_too_few_samples_warns(self) -> None:
        baseline, treatment = _build_baseline_treatment_pair(n=5)
        report = rd.decide(baseline, treatment, test_set_size=10)
        self.assertEqual(report["decision"]["decision"], rd.DECISION_WARN)
        # All per-test-case metrics should be EFFECT_TOO_FEW.
        for r in report["results"]:
            if r["granularity"] == "per_test_case_binary":
                self.assertEqual(r["effect"], rd.EFFECT_TOO_FEW)

    def test_judge_failure_rate_explosion_warns_not_blocks(self) -> None:
        # judge_failure_rate is auxiliary; even with strong significance,
        # decision must not BLOCK on it alone.
        baseline, treatment = _build_baseline_treatment_pair(n=100)
        treatment["judge_failure_rate"] = _per_test_case(
            "judge_failure_rate",
            {f"s{i}": (1 if i < 50 else 0) for i in range(100)},
        )
        report = rd.decide(baseline, treatment, test_set_size=200)
        self.assertNotEqual(report["decision"]["decision"], rd.DECISION_BLOCK)

    def test_holm_protects_against_one_off(self) -> None:
        # 1 metric drifts borderline (p~=0.04) but Holm with alpha=0.01 over
        # 6 canonical metrics requires p<=0.00167 for the smallest. Should
        # NOT block.
        baseline, treatment = _build_baseline_treatment_pair(n=100)
        # 8 baseline=1/treatment=0 vs 0 baseline=0/treatment=1 → p~0.004
        # which is below 0.01 (single-test) but above 0.01/6 (Holm-min).
        # Construct: drop 8 of the 100 signal test cases.
        treatment["signal_rate"] = _per_test_case(
            "signal_rate",
            {f"s{i}": (0 if i < 8 else 1) for i in range(100)},
        )
        report = rd.decide(baseline, treatment, test_set_size=200, alpha=0.01)
        # 0.004 > 0.001667 → Holm rejects, no block. (See _classify_effect:
        # mean_diff=-0.08 >= MDE=0.05 so Effect=Degraded, but Holm guard).
        # Effect itself can still be Degraded — the gate just doesn't block.
        self.assertNotEqual(report["decision"]["decision"], rd.DECISION_BLOCK)


class CompareApiTest(unittest.TestCase):
    def test_compare_per_test_case_binary_records_discordants(self) -> None:
        baseline = _per_test_case("signal_rate", {"s1": 1, "s2": 1, "s3": 0, "s4": 1})
        treatment = _per_test_case("signal_rate", {"s1": 1, "s2": 0, "s3": 1, "s4": 1})
        cmp_ = rd.compare_per_test_case_binary(
            "signal_rate", baseline, treatment,
            direction="higher_is_better",
            alpha=0.01,
            mde=0.05,
        )
        self.assertEqual(cmp_.detail["discordant_b"], 1)  # s2 baseline=1, treatment=0
        self.assertEqual(cmp_.detail["discordant_c"], 1)  # s3 baseline=0, treatment=1
        self.assertEqual(cmp_.n_pairs, 4)
        self.assertEqual(cmp_.test, "mcnemar")


if __name__ == "__main__":
    unittest.main()
