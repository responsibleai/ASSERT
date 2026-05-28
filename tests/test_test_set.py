# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the shared test-case sampling engine."""

import random
import unittest
from itertools import combinations, product

from p2m.analysis.stratification_metrics import (
    coverage_metrics,
    labeler_retest_agreement,
    normalized_entropy,
)
from p2m.core.io import stratification_dimensions, fill_template
from p2m.stages.stratification import (
    build_behavior_factor,
    normalize_stratification,
)
from p2m.stages.test_set import (
    build_covering_array,
    build_generation_jobs,
    build_generation_prompt,
    sample_from_covering_array,
    test_case_record as make_test_case_record,
)

FACTOR_NAMES = ("domain", "user_context")


def _make_policy(
    behavior_categories: list[tuple[str, str, bool]] | None = None,
) -> dict[str, object]:
    if behavior_categories is None:
        behavior_categories = [
            ("Behavior A", "Definition of A", True),
            ("Behavior B", "Definition of B", False),
        ]
    return {
        "behavior": {"name": "test-behavior"},
        "behavior_categories": [
            {"name": name, "definition": definition, "permissible": permissible}
            for name, definition, permissible in behavior_categories
        ],
    }


def _make_factor_stratification(levels: int = 3) -> dict[str, list[dict[str, str]]]:
    return {
        factor_name: [
            {
                "name": f"{factor_name} {index}",
                "definition": f"{factor_name} definition {index}",
            }
            for index in range(levels)
        ]
        for factor_name in FACTOR_NAMES
    }


def _make_stratification_with_behavior(levels: int = 3) -> dict[str, list[dict[str, str]]]:
    stratification = normalize_stratification(
        _make_factor_stratification(levels),
        _make_policy(),
        factor_order=list(FACTOR_NAMES),
        inject_behavior=True,
    )
    return stratification


class NormalizeStratificationTest(unittest.TestCase):
    def test_normalize_stratification_injects_behavior_when_requested(self) -> None:
        stratification = normalize_stratification(
            _make_factor_stratification(),
            _make_policy(),
            factor_order=list(FACTOR_NAMES),
            inject_behavior=True,
        )
        self.assertEqual(list(stratification_dimensions(stratification)), list(FACTOR_NAMES))
        self.assertIn("behavior", stratification)

    def test_normalize_stratification_uses_stratification_behavior_subset(self) -> None:
        raw_stratification = {
            "behavior": [
                {"name": "Behavior B", "description": "Definition of B"},
            ],
            **_make_factor_stratification(2),
        }
        stratification = normalize_stratification(raw_stratification, _make_policy())
        self.assertEqual([entry["name"] for entry in stratification["behavior"]], ["Behavior B"])

    def test_normalize_stratification_allows_custom_behavior_not_in_policy(self) -> None:
        raw_stratification = {
            "behavior": [
                {"name": "custom_behavior", "description": "A brand-new probe"},
            ],
        }
        stratification = normalize_stratification(raw_stratification, _make_policy())
        self.assertEqual([entry["name"] for entry in stratification["behavior"]], ["custom_behavior"])
        self.assertEqual(stratification["behavior"][0]["description"], "A brand-new probe")

    def test_normalize_stratification_preserves_diverging_description(self) -> None:
        raw_stratification = {
            "behavior": [
                {
                    "name": "Behavior A",
                    "description": "A refined definition that differs from taxonomy",
                },
            ],
        }
        stratification = normalize_stratification(raw_stratification, _make_policy())
        self.assertEqual(
            stratification["behavior"][0]["description"],
            "A refined definition that differs from taxonomy",
        )

    def test_normalize_stratification_rejects_factor_mismatch_against_configured_order(self) -> None:
        raw_stratification = {"domain": [{"name": "A", "definition": "B"}]}
        with self.assertRaises(ValueError):
            normalize_stratification(
                raw_stratification,
                _make_policy(),
                factor_order=["domain", "user_context"],
                inject_behavior=True,
            )

    def test_normalize_stratification_accepts_behavior_absent_mode(self) -> None:
        stratification = normalize_stratification(_make_factor_stratification(), _make_policy())
        self.assertNotIn("behavior", stratification)


class BuildGenerationJobsDisentangledTest(unittest.TestCase):
    def test_custom_behavior_without_policy_entry_produces_jobs(self) -> None:
        taxonomy = _make_policy()
        raw_stratification = {
            "behavior": [
                {
                    "name": "custom_probe",
                    "description": "A custom probe not in taxonomy",
                },
            ],
            **_make_factor_stratification(2),
        }
        stratification = normalize_stratification(raw_stratification, taxonomy)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy, stratification=stratification, sample_size=4, rng=random.Random(0),
        )
        self.assertTrue(jobs)
        for job in jobs:
            self.assertEqual(job.behavior["name"], "custom_probe")
            self.assertEqual(
                job.behavior["description"],
                "A custom probe not in taxonomy",
            )

    def test_renamed_description_is_used_verbatim(self) -> None:
        taxonomy = _make_policy(behavior_categories=[("Behavior A", "taxonomy definition", True)])
        raw_stratification = {
            "behavior": [
                {
                    "name": "Behavior A",
                    "description": "suite-specific refined definition",
                },
            ],
            **_make_factor_stratification(2),
        }
        stratification = normalize_stratification(raw_stratification, taxonomy)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy, stratification=stratification, sample_size=2, rng=random.Random(0),
        )
        self.assertEqual(
            jobs[0].behavior["description"],
            "suite-specific refined definition",
        )


class CoveringArrayTest(unittest.TestCase):
    def test_build_covering_array_covers_all_pair_cells(self) -> None:
        stratification = _make_stratification_with_behavior(3)
        ca = build_covering_array(stratification, random.Random(42), axes=FACTOR_NAMES)
        for factor_a, factor_b in combinations(FACTOR_NAMES, 2):
            observed = {(row[factor_a], row[factor_b]) for row in ca}
            possible = len(stratification[factor_a]) * len(stratification[factor_b])
            self.assertEqual(len(observed), possible)

    def test_sample_from_covering_array_returns_requested_count(self) -> None:
        stratification = _make_stratification_with_behavior(3)
        ca = build_covering_array(stratification, random.Random(42), axes=FACTOR_NAMES)
        drawn = sample_from_covering_array(ca, 4, random.Random(7))
        self.assertEqual(len(drawn), 4)


class FillTemplateTest(unittest.TestCase):
    def test_fill_template_replaces_placeholders(self) -> None:
        result = fill_template(
            "Hello {{name}}, welcome to {{place}}.",
            {"name": "Alice", "place": "Wonderland"},
        )
        self.assertEqual(result, "Hello Alice, welcome to Wonderland.")

    def test_fill_template_raises_on_missing_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            fill_template("Hello {{name}}, age {{age}}.", {"name": "Bob"})


class NormalizedEntropyTest(unittest.TestCase):
    def test_normalized_entropy_uniform_distribution(self) -> None:
        self.assertAlmostEqual(normalized_entropy([10, 10, 10, 10], 4), 1.0, places=6)

    def test_normalized_entropy_single_value(self) -> None:
        self.assertAlmostEqual(normalized_entropy([0, 0, 100], 3), 0.0, places=6)


class CoverageMetricsTest(unittest.TestCase):
    def test_coverage_metrics_full_coverage(self) -> None:
        stratification = _make_stratification_with_behavior(2)
        factor_order = tuple(key for key in stratification if not key.startswith("_"))
        all_names = {key: [entry["name"] for entry in stratification[key]] for key in factor_order}
        assignments = [
            {key: all_names[key][index] for key, index in zip(factor_order, combo)}
            for combo in product(*(range(2) for _ in factor_order))
        ]
        metrics = coverage_metrics(assignments, stratification)
        for value in metrics["per_factor_normalized_entropy"].values():
            self.assertAlmostEqual(value, 1.0, places=4)
        self.assertIn("factor_counts", metrics)
        self.assertIn("stratification_dimensions_pair_cell_coverage", metrics)


class LabelerRetestAgreementTest(unittest.TestCase):
    def test_labeler_retest_agreement_partial(self) -> None:
        stratification = _make_stratification_with_behavior(2)
        factor_order = tuple(key for key in stratification if not key.startswith("_"))
        labels_a = [
            {"test_case_id": "s1", **{key: stratification[key][0]["name"] for key in factor_order}},
            {"test_case_id": "s2", **{key: stratification[key][0]["name"] for key in factor_order}},
        ]
        labels_b = [
            {"test_case_id": "s1", **{key: stratification[key][0]["name"] for key in factor_order}},
            {"test_case_id": "s2", **{key: stratification[key][1]["name"] for key in factor_order}},
        ]
        result = labeler_retest_agreement(labels_a, labels_b, stratification)
        for factor_name in factor_order:
            self.assertAlmostEqual(result[factor_name], 0.5)


class SeedRecordTest(unittest.TestCase):
    def test_test_case_record_tags_prompt_kind(self) -> None:
        record = make_test_case_record(
            kind="prompt",
            test_case_id="ps-001",
            behavior="test-behavior",
            test_case_payload={"title": "t", "description": "d"},
        )
        self.assertEqual(record["type"], "prompt")
        self.assertEqual(record["test_case_id"], "ps-001")
        self.assertNotIn("permissible", record)

    def test_test_case_record_omits_empty_factors(self) -> None:
        record = make_test_case_record(
            kind="prompt",
            test_case_id="ps-001",
            behavior="test-behavior",
            test_case_payload={"title": "t", "description": "d"},
            dimensions={},
        )
        self.assertNotIn("dimensions", record)

    def test_test_case_record_persists_factors(self) -> None:
        record = make_test_case_record(
            kind="prompt",
            test_case_id="ps-001",
            behavior="test-behavior",
            test_case_payload={"title": "t", "description": "d"},
            dimensions={"domain": "domain 0"},
        )
        self.assertEqual(record["dimensions"], {"domain": "domain 0"})


class BuildGenerationPromptTest(unittest.TestCase):
    def _make_args(self, **overrides: object) -> dict[str, object]:
        taxonomy = _make_policy()
        stratification = _make_stratification_with_behavior(3)
        defaults = {
            "kind": "prompt",
            "taxonomy": taxonomy,
            "behavior": stratification["behavior"][0],
            "count": 4,
            "context": None,
            "stratification": stratification,
            "tuple_spec": {
                "domain": stratification["domain"][0],
                "user_context": stratification["user_context"][0],
                "behavior": stratification["behavior"][0],
            },
        }
        defaults.update(overrides)
        return defaults

    def test_build_generation_prompt_includes_assignments(self) -> None:
        prompt = build_generation_prompt(**self._make_args())
        self.assertIn("Exact Stratification Assignment", prompt)
        self.assertIn("Behavior A", prompt)

    def test_build_generation_prompt_injects_taxonomy_body(self) -> None:
        taxonomy = _make_policy(behavior_categories=[("Behavior A", "A-specific definition", True)])
        stratification = _make_stratification_with_behavior(3)
        # Override the behavior level so it matches the taxonomy.
        stratification["behavior"] = [{"name": "Behavior A", "description": "A-specific definition"}]
        args = {
            "kind": "scenario",
            "taxonomy": taxonomy,
            "behavior": stratification["behavior"][0],
            "count": 2,
            "context": None,
            "stratification": stratification,
            "tuple_spec": {
                "domain": stratification["domain"][0],
                "user_context": stratification["user_context"][0],
                "behavior": stratification["behavior"][0],
            },
        }
        prompt = build_generation_prompt(**args)
        self.assertIn("Taxonomy context", prompt)
        self.assertIn("A-specific definition", prompt)
        self.assertNotIn("{{taxonomy_body}}", prompt)
        self.assertNotIn("permissible_status", prompt)
        self.assertNotIn("test_case_strategy", prompt)


class BuildGenerationJobsTest(unittest.TestCase):
    def test_generation_jobs_cover_behavior_subset(self) -> None:
        taxonomy = _make_policy()
        stratification = normalize_stratification(
            {
                "behavior": [{"name": "Behavior B", "description": "Definition of B"}],
                **_make_factor_stratification(2),
            },
            taxonomy,
        )
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=4,
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), 4)
        self.assertIsNotNone(assignments)
        self.assertEqual({row["behavior"] for row in assignments or []}, {"Behavior B"})

    def test_generation_jobs_always_include_behavior_in_assignments(self) -> None:
        taxonomy = _make_policy()
        stratification = normalize_stratification(_make_factor_stratification(2), taxonomy, inject_behavior=True)
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=6,
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), 6)
        self.assertIsNotNone(assignments)
        self.assertTrue(all("behavior" in row for row in assignments or []))
        self.assertTrue(
            all(set(row) == {"behavior", *FACTOR_NAMES} for row in assignments or [])
        )

    def test_generation_jobs_with_behavior_only_stratification(self) -> None:
        taxonomy = _make_policy()
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            stratification={"behavior": build_behavior_factor(taxonomy)},
            sample_size=3,
            rng=random.Random(0),
        )
        self.assertEqual(sum(job.count for job in jobs), 3)
        self.assertTrue(all(set(row) == {"behavior"} for row in assignments or []))

    def test_generation_jobs_one_job_per_tuple(self) -> None:
        """Each covering-array tuple gets one job when its count fits inside
        MAX_TEST_CASES_PER_BATCH (here count == 1). Tuples whose count exceeds
        the cap are split into multiple jobs (covered by
        ``test_generation_jobs_split_when_per_tuple_count_exceeds_cap``)."""
        taxonomy = _make_policy()
        stratification = _make_stratification_with_behavior(2)
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=len(build_covering_array(stratification, random.Random(42), axes=("behavior",) + FACTOR_NAMES)),
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), len(jobs))
        self.assertTrue(all(job.count == 1 for job in jobs))

    def test_generation_jobs_budget_allocation_divmod(self) -> None:
        """Budget spreads evenly with remainder going to first tuples.
        Counts here (2 or 3) all fit inside MAX_TEST_CASES_PER_BATCH so each
        tuple still produces exactly one job."""
        taxonomy = _make_policy()
        stratification = _make_stratification_with_behavior(2)
        ca = build_covering_array(stratification, random.Random(42), axes=("behavior",) + FACTOR_NAMES)
        num_tuples = len(ca)
        sample_size = num_tuples * 2 + 3
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=sample_size,
            rng=random.Random(42),
        )
        self.assertEqual(len(jobs), num_tuples)
        self.assertEqual(sum(job.count for job in jobs), sample_size)
        counts = [job.count for job in jobs]
        self.assertEqual(counts.count(3), 3)
        self.assertEqual(counts.count(2), num_tuples - 3)

    def test_generation_jobs_sample_size_smaller_than_array(self) -> None:
        """When sample_size < covering array, some tuples get 0 and are skipped."""
        taxonomy = _make_policy()
        stratification = _make_stratification_with_behavior(2)
        sample_size = 3
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=sample_size,
            rng=random.Random(42),
        )
        self.assertEqual(len(jobs), sample_size)
        self.assertTrue(all(job.count == 1 for job in jobs))
        self.assertEqual(sum(job.count for job in jobs), sample_size)

    def test_generation_jobs_start_index_contiguous(self) -> None:
        """start_index values are contiguous across all jobs."""
        taxonomy = _make_policy()
        stratification = normalize_stratification(_make_factor_stratification(2), taxonomy, inject_behavior=True)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=10,
            rng=random.Random(42),
        )
        running = 0
        for job in jobs:
            self.assertEqual(job.start_index, running)
            running += job.count

    def test_generation_jobs_singular_tuple_spec(self) -> None:
        """Each job carries a singular tuple_spec dict, not a list."""
        taxonomy = _make_policy()
        stratification = normalize_stratification(_make_factor_stratification(2), taxonomy, inject_behavior=True)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=6,
            rng=random.Random(42),
        )
        for job in jobs:
            self.assertIsInstance(job.tuple_spec, dict)
            self.assertIn("behavior", job.tuple_spec)
            for factor_name in FACTOR_NAMES:
                self.assertIn(factor_name, job.tuple_spec)

    def test_generation_jobs_split_when_per_tuple_count_exceeds_cap(self) -> None:
        """When a covering-array tuple's budget exceeds MAX_TEST_CASES_PER_BATCH,
        the tuple produces multiple jobs whose counts are each ≤ the cap and
        whose start_index slots remain contiguous within the tuple."""
        from p2m.stages.test_set import MAX_TEST_CASES_PER_BATCH

        taxonomy = _make_policy()
        stratification = _make_stratification_with_behavior(2)
        ca = build_covering_array(
            stratification, random.Random(42), axes=("behavior",) + FACTOR_NAMES
        )
        num_tuples = len(ca)
        per_tuple = MAX_TEST_CASES_PER_BATCH * 3 + 2
        sample_size = num_tuples * per_tuple
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=sample_size,
            rng=random.Random(42),
        )

        self.assertEqual(sum(job.count for job in jobs), sample_size)
        self.assertTrue(all(job.count <= MAX_TEST_CASES_PER_BATCH for job in jobs))

        expected_jobs_per_tuple = (
            per_tuple + MAX_TEST_CASES_PER_BATCH - 1
        ) // MAX_TEST_CASES_PER_BATCH
        self.assertEqual(len(jobs), num_tuples * expected_jobs_per_tuple)

        running = 0
        for job in jobs:
            self.assertEqual(job.start_index, running)
            running += job.count

    def test_generation_jobs_no_split_when_count_within_cap(self) -> None:
        """When per-tuple count fits inside MAX_TEST_CASES_PER_BATCH, each tuple
        still produces exactly one job (covers the small-batch case)."""
        from p2m.stages.test_set import MAX_TEST_CASES_PER_BATCH

        taxonomy = _make_policy()
        stratification = _make_stratification_with_behavior(2)
        ca = build_covering_array(
            stratification, random.Random(42), axes=("behavior",) + FACTOR_NAMES
        )
        num_tuples = len(ca)
        sample_size = num_tuples * MAX_TEST_CASES_PER_BATCH
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=sample_size,
            rng=random.Random(42),
        )
        self.assertEqual(len(jobs), num_tuples)
        self.assertTrue(all(job.count == MAX_TEST_CASES_PER_BATCH for job in jobs))


class BuildPolicyNodeFactorTest(unittest.TestCase):
    def test_build_behavior_factor_creates_entries_from_behavior_categories(self) -> None:
        dimension = build_behavior_factor(_make_policy())
        self.assertEqual(
            [entry["name"] for entry in dimension],
            ["Behavior A", "Behavior B"],
        )

    def test_build_behavior_factor_raises_on_missing_name(self) -> None:
        with self.assertRaises(ValueError):
            build_behavior_factor({"behavior_categories": [{"name": "", "definition": "def"}]})

    def test_build_behavior_factor_returns_name_and_description_only(self) -> None:
        taxonomy = {
            "behavior_categories": [
                {
                    "name": "B1",
                    "definition": "d1",
                    "permissible": False,
                    "examples": ["ex1", "ex2"],
                },
            ],
        }
        dimension = build_behavior_factor(taxonomy)
        self.assertEqual(dimension[0], {"name": "B1", "description": "d1"})


if __name__ == "__main__":
    unittest.main()
