"""Tests for the test-set sampling module."""

from __future__ import annotations

import random
import unittest
from collections import Counter

from p2m.stages.sampling import (
    resolve_sampling_config,
    sample_assignments,
    validate_sampling_shape,
)


def _design(level_counts: dict[str, int]) -> dict[str, list[dict[str, str]]]:
    return {
        axis: [
            {"name": f"{axis}_{i}", "definition": f"{axis} level {i}"}
            for i in range(n)
        ]
        for axis, n in level_counts.items()
    }


class ValidateSamplingShapeTest(unittest.TestCase):
    def test_default_when_sampling_is_none(self) -> None:
        cfg = validate_sampling_shape(None)
        self.assertEqual(cfg, {"method": "stratified", "stratify_by": ["behavior"]})

    def test_unknown_method_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sampling.method"):
            validate_sampling_shape({"method": "magic"})

    def test_method_must_be_string(self) -> None:
        with self.assertRaisesRegex(ValueError, "sampling.method must be a string"):
            validate_sampling_shape({"method": ["random"]})

    def test_unknown_key_for_method_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            validate_sampling_shape({"method": "random", "fill": "none"})

    def test_sampling_must_be_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "sampling must be a mapping"):
            validate_sampling_shape("stratified")  # type: ignore[arg-type]

    def test_stratified_rejects_empty_stratify_by(self) -> None:
        with self.assertRaisesRegex(ValueError, "stratify_by"):
            validate_sampling_shape({"method": "stratified", "stratify_by": []})

    def test_stratified_rejects_non_string_axis(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be strings"):
            validate_sampling_shape(
                {"method": "stratified", "stratify_by": ["behavior", 42]}
            )

    def test_stratified_rejects_duplicate_axes(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            validate_sampling_shape(
                {"method": "stratified", "stratify_by": ["behavior", "behavior"]}
            )

    def test_stratified_default_stratify_by_is_behavior(self) -> None:
        cfg = validate_sampling_shape({"method": "stratified"})
        self.assertEqual(cfg["stratify_by"], ["behavior"])

    def test_full_factorial_rejects_bad_replication(self) -> None:
        with self.assertRaisesRegex(ValueError, "replication"):
            validate_sampling_shape(
                {"method": "full_factorial", "replication": "magic"}
            )

    def test_full_factorial_default_replication_is_balanced(self) -> None:
        cfg = validate_sampling_shape({"method": "full_factorial"})
        self.assertEqual(cfg["replication"], "balanced")

    def test_random_default_with_replacement_is_true(self) -> None:
        cfg = validate_sampling_shape({"method": "random"})
        self.assertIs(cfg["with_replacement"], True)


class ResolveSamplingConfigTest(unittest.TestCase):
    def test_resolve_rejects_unknown_stratify_axis(self) -> None:
        design = _design({"behavior": 2, "domain": 2})
        with self.assertRaisesRegex(ValueError, "stratify_by axis 'nope'"):
            resolve_sampling_config(
                {"method": "stratified", "stratify_by": ["nope"]},
                design=design,
            )

    def test_resolve_passes_through_for_random(self) -> None:
        design = _design({"behavior": 2, "domain": 2})
        cfg = resolve_sampling_config({"method": "random"}, design=design)
        self.assertEqual(cfg, {"method": "random", "with_replacement": True})


class StratifiedTest(unittest.TestCase):
    def test_default_stratifies_by_behavior(self) -> None:
        design = _design({"behavior": 4, "domain": 2})
        rows = sample_assignments(
            design=design,
            sample_size=8,
            sampling=None,
            rng=random.Random(0),
        )
        counts = Counter(row["behavior"] for row in rows)
        self.assertEqual(counts, {"behavior_0": 2, "behavior_1": 2, "behavior_2": 2, "behavior_3": 2})

    def test_sample_size_one_against_many_strata_returns_one_row(self) -> None:
        design = _design({"behavior": 7, "domain": 2})
        rows = sample_assignments(
            design=design,
            sample_size=1,
            sampling=None,
            rng=random.Random(0),
        )
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0]["behavior"], {b["name"] for b in design["behavior"]})

    def test_can_stratify_by_behavior_and_factor(self) -> None:
        design = _design({"behavior": 2, "domain": 2, "user": 3})
        rows = sample_assignments(
            design=design,
            sample_size=8,
            sampling={"method": "stratified", "stratify_by": ["behavior", "domain"]},
            rng=random.Random(0),
        )
        counts = Counter((row["behavior"], row["domain"]) for row in rows)
        self.assertEqual(set(counts.values()), {2})
        self.assertEqual(len(counts), 4)


class FullFactorialTest(unittest.TestCase):
    def test_balanced_replication_covers_every_cell(self) -> None:
        design = _design({"behavior": 2, "domain": 2})
        rows = sample_assignments(
            design=design,
            sample_size=8,
            sampling={"method": "full_factorial"},
            rng=random.Random(0),
        )
        counts = Counter((row["behavior"], row["domain"]) for row in rows)
        self.assertEqual(set(counts.values()), {2})
        self.assertEqual(len(counts), 4)

    def test_balanced_errors_when_budget_below_full_size(self) -> None:
        design = _design({"behavior": 2, "domain": 3})
        with self.assertRaisesRegex(ValueError, "sample_size .* >= full factorial size"):
            sample_assignments(
                design=design,
                sample_size=3,
                sampling={"method": "full_factorial"},
                rng=random.Random(0),
            )

    def test_replication_none_requires_exact_full_size(self) -> None:
        design = _design({"behavior": 2, "domain": 2})
        rows = sample_assignments(
            design=design,
            sample_size=4,
            sampling={"method": "full_factorial", "replication": "none"},
            rng=random.Random(0),
        )
        self.assertEqual(len({(row["behavior"], row["domain"]) for row in rows}), 4)


class RandomSamplingTest(unittest.TestCase):
    def test_random_with_replacement_allows_duplicates(self) -> None:
        design = _design({"behavior": 2, "domain": 2})
        rows = sample_assignments(
            design=design,
            sample_size=50,
            sampling={"method": "random"},
            rng=random.Random(0),
        )
        self.assertEqual(len(rows), 50)
        self.assertLess(len({(row["behavior"], row["domain"]) for row in rows}), 50)

    def test_random_without_replacement_requires_capacity(self) -> None:
        design = _design({"behavior": 2, "domain": 2})
        with self.assertRaisesRegex(ValueError, "with_replacement=false"):
            sample_assignments(
                design=design,
                sample_size=5,
                sampling={"method": "random", "with_replacement": False},
                rng=random.Random(0),
            )


if __name__ == "__main__":
    unittest.main()
