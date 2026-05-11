"""Unit tests for ``scripts.regression_metrics``.

Synthetic-data tests covering all 10 metrics. No live LLM calls. Every
assertion is grounded in a hand-constructed ``scores.jsonl``-style fixture
plus a ``policy.json``-style taxonomy.
"""

from __future__ import annotations

import unittest
from typing import Any

from scripts import regression_metrics as rm


def _score(
    seed_id: str,
    behavior: str,
    *,
    judge_status: str = "ok",
    policy_violation: bool | None = False,
    overrefusal: bool | None = False,
) -> dict[str, Any]:
    """Build a synthetic scores.jsonl row with realistic verdict shape.

    A judge verdict is considered "successful" only when both built-in
    dimensions (``policy_violation``, ``overrefusal``) are present as
    booleans AND ``node_judgments`` is a list. We always satisfy that
    contract for ``judge_status="ok"`` rows so ``infer_judge_status``
    returns "ok".
    """
    if judge_status != "ok":
        return {
            "seed_id": seed_id,
            "factors": {"behavior": behavior},
            "judge_status": judge_status,
            "verdict": None,
        }
    dimensions: dict[str, Any] = {
        "policy_violation": False if policy_violation is None else policy_violation,
        "overrefusal": False if overrefusal is None else overrefusal,
    }
    return {
        "seed_id": seed_id,
        "factors": {"behavior": behavior},
        "judge_status": judge_status,
        "verdict": {"dimensions": dimensions, "node_judgments": []},
    }


def _policy(*behaviors: dict[str, Any]) -> dict[str, Any]:
    return {"behaviors": list(behaviors)}


# Realistic small taxonomy: 4 behaviors, mixed permissibility.
POLICY = _policy(
    {"name": "advisory_ignore", "permissible": False},
    {"name": "danger_destination", "permissible": False},
    {"name": "private_data_share", "permissible": False},
    {"name": "general_travel_help", "permissible": True},
)


class SignalRateTest(unittest.TestCase):
    def test_all_signal(self) -> None:
        rows = [
            _score("s1", "advisory_ignore", policy_violation=True),
            _score("s2", "general_travel_help", policy_violation=False),
        ]
        result = rm.signal_rate(rows)
        self.assertEqual(result.value, 1.0)
        self.assertEqual(result.granularity, "per_seed_binary")
        self.assertEqual(set(result.per_seed.values()), {1})

    def test_judge_failed_is_noise(self) -> None:
        rows = [
            _score("s1", "advisory_ignore", policy_violation=True),
            _score("s2", "general_travel_help", judge_status="judge_failed",
                   policy_violation=None, overrefusal=None),
        ]
        result = rm.signal_rate(rows)
        self.assertEqual(result.value, 0.5)
        self.assertEqual(result.per_seed["s1"], 1)
        self.assertEqual(result.per_seed["s2"], 0)

    def test_empty(self) -> None:
        self.assertEqual(rm.signal_rate([]).value, 0.0)


class PolicyViolationRateTest(unittest.TestCase):
    def test_basic(self) -> None:
        rows = [
            _score("s1", "danger_destination", policy_violation=True),
            _score("s2", "general_travel_help", policy_violation=False),
            _score("s3", "advisory_ignore", policy_violation=True),
            _score("s4", "general_travel_help", policy_violation=False),
        ]
        result = rm.policy_violation_rate(rows)
        self.assertEqual(result.value, 0.5)
        self.assertEqual(len(result.per_seed), 4)

    def test_skips_judge_failed(self) -> None:
        rows = [
            _score("s1", "danger_destination", policy_violation=True),
            _score("s2", "general_travel_help", judge_status="judge_failed",
                   policy_violation=None, overrefusal=None),
        ]
        result = rm.policy_violation_rate(rows)
        # Only s1 counts.
        self.assertEqual(result.value, 1.0)
        self.assertEqual(set(result.per_seed), {"s1"})


class OverrefusalRateTest(unittest.TestCase):
    def test_basic(self) -> None:
        rows = [
            _score("s1", "general_travel_help", policy_violation=False, overrefusal=True),
            _score("s2", "general_travel_help", policy_violation=False, overrefusal=False),
        ]
        result = rm.overrefusal_rate(rows)
        self.assertEqual(result.value, 0.5)


class JudgeFailureRateTest(unittest.TestCase):
    def test_includes_judge_failed(self) -> None:
        rows = [
            _score("s1", "x", policy_violation=True),
            _score("s2", "y", judge_status="judge_failed", policy_violation=None, overrefusal=None),
        ]
        result = rm.judge_failure_rate(rows)
        self.assertEqual(result.value, 0.5)
        self.assertEqual(result.per_seed["s2"], 1)
        self.assertEqual(result.per_seed["s1"], 0)


class ConstructCoverageTest(unittest.TestCase):
    def test_all_covered(self) -> None:
        rows = [
            _score("s1", "advisory_ignore"),
            _score("s2", "danger_destination"),
            _score("s3", "private_data_share"),
            _score("s4", "general_travel_help"),
        ]
        result = rm.construct_coverage(rows, POLICY)
        self.assertEqual(result.value, 1.0)
        self.assertEqual(result.detail["covered"], 4)

    def test_partial(self) -> None:
        rows = [
            _score("s1", "advisory_ignore"),
            _score("s2", "advisory_ignore"),  # repeated, doesn't add
        ]
        result = rm.construct_coverage(rows, POLICY)
        self.assertAlmostEqual(result.value, 0.25)

    def test_empty_policy(self) -> None:
        rows = [_score("s1", "x")]
        self.assertEqual(rm.construct_coverage(rows, {}).value, 0.0)
        self.assertEqual(rm.construct_coverage(rows, None).value, 0.0)


class SeparationStrengthTest(unittest.TestCase):
    def test_clean_separation(self) -> None:
        # Non-permissible behaviors all violated, permissible all clear:
        # nonperm_rate = 1.0, perm_rate = 0.0.
        rows = [
            _score("s1", "advisory_ignore", policy_violation=True),
            _score("s2", "danger_destination", policy_violation=True),
            _score("s3", "general_travel_help", policy_violation=False),
            _score("s4", "general_travel_help", policy_violation=False),
        ]
        result = rm.separation_strength(rows, POLICY)
        self.assertEqual(result.value, 1.0)

    def test_inverted_pipeline_floors_at_zero(self) -> None:
        # Permissible flagged, non-permissible cleared. separation_strength
        # is bounded below at 0; discrimination_power can go negative.
        rows = [
            _score("s1", "advisory_ignore", policy_violation=False),
            _score("s2", "general_travel_help", policy_violation=True),
        ]
        sep = rm.separation_strength(rows, POLICY)
        disc = rm.discrimination_power(rows, POLICY)
        self.assertEqual(sep.value, 0.0)
        self.assertLess(disc.value, 0.0)


class FailureVarietyTest(unittest.TestCase):
    def test_count_distinct_violated_behaviors(self) -> None:
        rows = [
            _score("s1", "advisory_ignore", policy_violation=True),
            _score("s2", "advisory_ignore", policy_violation=True),  # dup
            _score("s3", "danger_destination", policy_violation=True),
            _score("s4", "general_travel_help", policy_violation=False),
        ]
        result = rm.failure_variety(rows)
        self.assertEqual(result.value, 2.0)
        self.assertEqual(set(result.detail["behaviors"]), {"advisory_ignore", "danger_destination"})


class ItemSaturationTest(unittest.TestCase):
    def test_diverse_rows_low_saturation(self) -> None:
        rows = [
            _score("s1", "advisory_ignore", policy_violation=True),
            _score("s2", "danger_destination", policy_violation=False),
            _score("s3", "private_data_share", policy_violation=True),
        ]
        result = rm.item_saturation(rows)
        # 3 distinct (behavior, verdict) sigs in 3 rows → 1 - 1.0 = 0.
        self.assertEqual(result.value, 0.0)

    def test_redundant_rows_high_saturation(self) -> None:
        # Same behavior + same verdict 5 times → 1 distinct sig in 5 rows → 1 - 0.2 = 0.8.
        rows = [
            _score(f"s{i}", "advisory_ignore", policy_violation=True) for i in range(5)
        ]
        result = rm.item_saturation(rows)
        self.assertAlmostEqual(result.value, 0.8)


class ComputeAllTest(unittest.TestCase):
    def test_returns_all_metric_keys(self) -> None:
        rows = [
            _score("s1", "advisory_ignore", policy_violation=True),
            _score("s2", "general_travel_help", policy_violation=False),
        ]
        all_results = rm.compute_all(rows, POLICY)
        expected = set(rm.CANONICAL_METRICS) | set(rm.AUXILIARY_METRICS)
        self.assertEqual(set(all_results), expected)
        for name, result in all_results.items():
            self.assertEqual(result.name, name)


if __name__ == "__main__":
    unittest.main()
