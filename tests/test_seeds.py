"""Tests for the shared seed sampling engine."""

import random
import unittest
from itertools import combinations, product

from p2m.analysis.design_metrics import (
    coverage_metrics,
    labeler_retest_agreement,
    normalized_entropy,
)
from p2m.core.io import design_factors, fill_template
from p2m.stages.design import (
    build_behavior_factor,
    normalize_design,
)
from p2m.stages.seeds import (
    build_covering_array,
    build_generation_jobs,
    build_generation_prompt,
    sample_from_covering_array,
    seed_record,
)

FACTOR_NAMES = ("domain", "user_context")


def _make_policy(
    behaviors: list[tuple[str, str, bool]] | None = None,
) -> dict[str, object]:
    if behaviors is None:
        behaviors = [
            ("Behavior A", "Definition of A", True),
            ("Behavior B", "Definition of B", False),
        ]
    return {
        "concept": {"name": "test-concept"},
        "behaviors": [
            {"name": name, "definition": definition, "permissible": permissible}
            for name, definition, permissible in behaviors
        ],
    }


def _make_factor_design(levels: int = 3) -> dict[str, list[dict[str, str]]]:
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


def _make_design_with_behavior(levels: int = 3) -> dict[str, list[dict[str, str]]]:
    design = normalize_design(
        _make_factor_design(levels),
        _make_policy(),
        factor_order=list(FACTOR_NAMES),
        inject_behavior=True,
    )
    return design


class NormalizeDesignTest(unittest.TestCase):
    def test_normalize_design_injects_behavior_when_requested(self) -> None:
        design = normalize_design(
            _make_factor_design(),
            _make_policy(),
            factor_order=list(FACTOR_NAMES),
            inject_behavior=True,
        )
        self.assertEqual(list(design_factors(design)), list(FACTOR_NAMES))
        self.assertIn("behavior", design)

    def test_normalize_design_uses_design_behavior_subset(self) -> None:
        raw_design = {
            "behavior": [
                {"name": "Behavior B", "description": "Definition of B"},
            ],
            **_make_factor_design(2),
        }
        design = normalize_design(raw_design, _make_policy())
        self.assertEqual([entry["name"] for entry in design["behavior"]], ["Behavior B"])

    def test_normalize_design_allows_custom_behavior_not_in_policy(self) -> None:
        raw_design = {
            "behavior": [
                {"name": "custom_behavior", "description": "A brand-new probe"},
            ],
        }
        design = normalize_design(raw_design, _make_policy())
        self.assertEqual([entry["name"] for entry in design["behavior"]], ["custom_behavior"])
        self.assertEqual(design["behavior"][0]["description"], "A brand-new probe")

    def test_normalize_design_preserves_diverging_description(self) -> None:
        raw_design = {
            "behavior": [
                {
                    "name": "Behavior A",
                    "description": "A refined definition that differs from policy",
                },
            ],
        }
        design = normalize_design(raw_design, _make_policy())
        self.assertEqual(
            design["behavior"][0]["description"],
            "A refined definition that differs from policy",
        )

    def test_normalize_design_rejects_factor_mismatch_against_configured_order(self) -> None:
        raw_design = {"domain": [{"name": "A", "definition": "B"}]}
        with self.assertRaises(ValueError):
            normalize_design(
                raw_design,
                _make_policy(),
                factor_order=["domain", "user_context"],
                inject_behavior=True,
            )

    def test_normalize_design_accepts_behavior_absent_mode(self) -> None:
        design = normalize_design(_make_factor_design(), _make_policy())
        self.assertNotIn("behavior", design)


class BuildGenerationJobsDisentangledTest(unittest.TestCase):
    def test_custom_behavior_without_policy_entry_produces_jobs(self) -> None:
        policy = _make_policy()
        raw_design = {
            "behavior": [
                {
                    "name": "custom_probe",
                    "description": "A custom probe not in policy",
                },
            ],
            **_make_factor_design(2),
        }
        design = normalize_design(raw_design, policy)
        jobs, _ = build_generation_jobs(
            policy=policy, design=design, sample_size=4, rng=random.Random(0),
        )
        self.assertTrue(jobs)
        for job in jobs:
            self.assertEqual(job.behavior["name"], "custom_probe")
            self.assertEqual(
                job.behavior["description"],
                "A custom probe not in policy",
            )

    def test_renamed_description_is_used_verbatim(self) -> None:
        policy = _make_policy(behaviors=[("Behavior A", "policy definition", True)])
        raw_design = {
            "behavior": [
                {
                    "name": "Behavior A",
                    "description": "suite-specific refined definition",
                },
            ],
            **_make_factor_design(2),
        }
        design = normalize_design(raw_design, policy)
        jobs, _ = build_generation_jobs(
            policy=policy, design=design, sample_size=2, rng=random.Random(0),
        )
        self.assertEqual(
            jobs[0].behavior["description"],
            "suite-specific refined definition",
        )


class CoveringArrayTest(unittest.TestCase):
    def test_build_covering_array_covers_all_pair_cells(self) -> None:
        design = _make_design_with_behavior(3)
        ca = build_covering_array(design, random.Random(42), axes=FACTOR_NAMES)
        for factor_a, factor_b in combinations(FACTOR_NAMES, 2):
            observed = {(row[factor_a], row[factor_b]) for row in ca}
            possible = len(design[factor_a]) * len(design[factor_b])
            self.assertEqual(len(observed), possible)

    def test_sample_from_covering_array_returns_requested_count(self) -> None:
        design = _make_design_with_behavior(3)
        ca = build_covering_array(design, random.Random(42), axes=FACTOR_NAMES)
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
        design = _make_design_with_behavior(2)
        factor_order = tuple(key for key in design if not key.startswith("_"))
        all_names = {key: [entry["name"] for entry in design[key]] for key in factor_order}
        assignments = [
            {key: all_names[key][index] for key, index in zip(factor_order, combo)}
            for combo in product(*(range(2) for _ in factor_order))
        ]
        metrics = coverage_metrics(assignments, design)
        for value in metrics["per_factor_normalized_entropy"].values():
            self.assertAlmostEqual(value, 1.0, places=4)
        self.assertIn("factor_counts", metrics)
        self.assertIn("design_factors_pair_cell_coverage", metrics)


class LabelerRetestAgreementTest(unittest.TestCase):
    def test_labeler_retest_agreement_partial(self) -> None:
        design = _make_design_with_behavior(2)
        factor_order = tuple(key for key in design if not key.startswith("_"))
        labels_a = [
            {"seed_id": "s1", **{key: design[key][0]["name"] for key in factor_order}},
            {"seed_id": "s2", **{key: design[key][0]["name"] for key in factor_order}},
        ]
        labels_b = [
            {"seed_id": "s1", **{key: design[key][0]["name"] for key in factor_order}},
            {"seed_id": "s2", **{key: design[key][1]["name"] for key in factor_order}},
        ]
        result = labeler_retest_agreement(labels_a, labels_b, design)
        for factor_name in factor_order:
            self.assertAlmostEqual(result[factor_name], 0.5)


class SeedRecordTest(unittest.TestCase):
    def test_seed_record_tags_prompt_kind(self) -> None:
        record = seed_record(
            kind="prompt",
            seed_id="ps-001",
            concept="test-concept",
            seed_payload={"title": "t", "description": "d"},
        )
        self.assertEqual(record["kind"], "prompt")
        self.assertEqual(record["seed_id"], "ps-001")
        self.assertNotIn("permissible", record)

    def test_seed_record_omits_empty_factors(self) -> None:
        record = seed_record(
            kind="prompt",
            seed_id="ps-001",
            concept="test-concept",
            seed_payload={"title": "t", "description": "d"},
            factors={},
        )
        self.assertNotIn("factors", record)

    def test_seed_record_persists_factors(self) -> None:
        record = seed_record(
            kind="prompt",
            seed_id="ps-001",
            concept="test-concept",
            seed_payload={"title": "t", "description": "d"},
            factors={"domain": "domain 0"},
        )
        self.assertEqual(record["factors"], {"domain": "domain 0"})


class BuildGenerationPromptTest(unittest.TestCase):
    def _make_args(self, **overrides: object) -> dict[str, object]:
        policy = _make_policy()
        design = _make_design_with_behavior(3)
        defaults = {
            "kind": "prompt",
            "policy": policy,
            "behavior": design["behavior"][0],
            "count": 4,
            "context": None,
            "design": design,
            "tuple_spec": {
                "domain": design["domain"][0],
                "user_context": design["user_context"][0],
                "behavior": design["behavior"][0],
            },
        }
        defaults.update(overrides)
        return defaults

    def test_build_generation_prompt_includes_assignments(self) -> None:
        prompt = build_generation_prompt(**self._make_args())
        self.assertIn("Exact Design Assignment", prompt)
        self.assertIn("Behavior A", prompt)

    def test_build_generation_prompt_injects_policy_body(self) -> None:
        policy = _make_policy(behaviors=[("Behavior A", "A-specific definition", True)])
        design = _make_design_with_behavior(3)
        # Override the behavior level so it matches the policy.
        design["behavior"] = [{"name": "Behavior A", "description": "A-specific definition"}]
        args = {
            "kind": "scenario",
            "policy": policy,
            "behavior": design["behavior"][0],
            "count": 2,
            "context": None,
            "design": design,
            "tuple_spec": {
                "domain": design["domain"][0],
                "user_context": design["user_context"][0],
                "behavior": design["behavior"][0],
            },
        }
        prompt = build_generation_prompt(**args)
        self.assertIn("Policy context", prompt)
        self.assertIn("A-specific definition", prompt)
        self.assertNotIn("{{policy_body}}", prompt)
        self.assertNotIn("permissible_status", prompt)
        self.assertNotIn("seed_strategy", prompt)


class BuildGenerationJobsTest(unittest.TestCase):
    def test_generation_jobs_cover_behavior_subset(self) -> None:
        policy = _make_policy()
        design = normalize_design(
            {
                "behavior": [{"name": "Behavior B", "description": "Definition of B"}],
                **_make_factor_design(2),
            },
            policy,
        )
        jobs, assignments = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=4,
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), 4)
        self.assertIsNotNone(assignments)
        self.assertEqual({row["behavior"] for row in assignments or []}, {"Behavior B"})

    def test_generation_jobs_always_include_behavior_in_assignments(self) -> None:
        policy = _make_policy()
        design = normalize_design(_make_factor_design(2), policy, inject_behavior=True)
        jobs, assignments = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=6,
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), 6)
        self.assertIsNotNone(assignments)
        self.assertTrue(all("behavior" in row for row in assignments or []))
        self.assertTrue(
            all(set(row) == {"behavior", *FACTOR_NAMES} for row in assignments or [])
        )

    def test_generation_jobs_with_behavior_only_design(self) -> None:
        policy = _make_policy()
        jobs, assignments = build_generation_jobs(
            policy=policy,
            design={"behavior": build_behavior_factor(policy)},
            sample_size=3,
            rng=random.Random(0),
        )
        self.assertEqual(sum(job.count for job in jobs), 3)
        self.assertTrue(all(set(row) == {"behavior"} for row in assignments or []))

    def test_generation_jobs_one_job_per_tuple(self) -> None:
        """Each covering-array tuple gets one job when its count fits inside
        MAX_SEEDS_PER_BATCH (here count == 1). Tuples whose count exceeds
        the cap are split into multiple jobs (covered by
        ``test_generation_jobs_split_when_per_tuple_count_exceeds_cap``)."""
        policy = _make_policy()
        design = _make_design_with_behavior(2)
        jobs, assignments = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=len(build_covering_array(design, random.Random(42), axes=("behavior",) + FACTOR_NAMES)),
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), len(jobs))
        self.assertTrue(all(job.count == 1 for job in jobs))

    def test_generation_jobs_budget_allocation_divmod(self) -> None:
        """Budget spreads evenly with remainder going to first tuples.
        Counts here (2 or 3) all fit inside MAX_SEEDS_PER_BATCH so each
        tuple still produces exactly one job."""
        policy = _make_policy()
        design = _make_design_with_behavior(2)
        ca = build_covering_array(design, random.Random(42), axes=("behavior",) + FACTOR_NAMES)
        num_tuples = len(ca)
        sample_size = num_tuples * 2 + 3
        jobs, assignments = build_generation_jobs(
            policy=policy,
            design=design,
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
        policy = _make_policy()
        design = _make_design_with_behavior(2)
        sample_size = 3
        jobs, assignments = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=sample_size,
            rng=random.Random(42),
        )
        self.assertEqual(len(jobs), sample_size)
        self.assertTrue(all(job.count == 1 for job in jobs))
        self.assertEqual(sum(job.count for job in jobs), sample_size)

    def test_generation_jobs_start_index_contiguous(self) -> None:
        """start_index values are contiguous across all jobs."""
        policy = _make_policy()
        design = normalize_design(_make_factor_design(2), policy, inject_behavior=True)
        jobs, _ = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=10,
            rng=random.Random(42),
        )
        running = 0
        for job in jobs:
            self.assertEqual(job.start_index, running)
            running += job.count

    def test_generation_jobs_singular_tuple_spec(self) -> None:
        """Each job carries a singular tuple_spec dict, not a list."""
        policy = _make_policy()
        design = normalize_design(_make_factor_design(2), policy, inject_behavior=True)
        jobs, _ = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=6,
            rng=random.Random(42),
        )
        for job in jobs:
            self.assertIsInstance(job.tuple_spec, dict)
            self.assertIn("behavior", job.tuple_spec)
            for factor_name in FACTOR_NAMES:
                self.assertIn(factor_name, job.tuple_spec)

    def test_generation_jobs_split_when_per_tuple_count_exceeds_cap(self) -> None:
        """When a covering-array tuple's budget exceeds MAX_SEEDS_PER_BATCH,
        the tuple produces multiple jobs whose counts are each ≤ the cap and
        whose start_index slots remain contiguous within the tuple."""
        from p2m.stages.seeds import MAX_SEEDS_PER_BATCH

        policy = _make_policy()
        design = _make_design_with_behavior(2)
        ca = build_covering_array(
            design, random.Random(42), axes=("behavior",) + FACTOR_NAMES
        )
        num_tuples = len(ca)
        per_tuple = MAX_SEEDS_PER_BATCH * 3 + 2
        sample_size = num_tuples * per_tuple
        jobs, _ = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=sample_size,
            rng=random.Random(42),
        )

        self.assertEqual(sum(job.count for job in jobs), sample_size)
        self.assertTrue(all(job.count <= MAX_SEEDS_PER_BATCH for job in jobs))

        expected_jobs_per_tuple = (
            per_tuple + MAX_SEEDS_PER_BATCH - 1
        ) // MAX_SEEDS_PER_BATCH
        self.assertEqual(len(jobs), num_tuples * expected_jobs_per_tuple)

        running = 0
        for job in jobs:
            self.assertEqual(job.start_index, running)
            running += job.count

    def test_generation_jobs_no_split_when_count_within_cap(self) -> None:
        """When per-tuple count fits inside MAX_SEEDS_PER_BATCH, each tuple
        still produces exactly one job (covers the small-batch case)."""
        from p2m.stages.seeds import MAX_SEEDS_PER_BATCH

        policy = _make_policy()
        design = _make_design_with_behavior(2)
        ca = build_covering_array(
            design, random.Random(42), axes=("behavior",) + FACTOR_NAMES
        )
        num_tuples = len(ca)
        sample_size = num_tuples * MAX_SEEDS_PER_BATCH
        jobs, _ = build_generation_jobs(
            policy=policy,
            design=design,
            sample_size=sample_size,
            rng=random.Random(42),
        )
        self.assertEqual(len(jobs), num_tuples)
        self.assertTrue(all(job.count == MAX_SEEDS_PER_BATCH for job in jobs))


class BuildPolicyNodeFactorTest(unittest.TestCase):
    def test_build_behavior_factor_creates_entries_from_behaviors(self) -> None:
        factor = build_behavior_factor(_make_policy())
        self.assertEqual(
            [entry["name"] for entry in factor],
            ["Behavior A", "Behavior B"],
        )

    def test_build_behavior_factor_raises_on_missing_name(self) -> None:
        with self.assertRaises(ValueError):
            build_behavior_factor({"behaviors": [{"name": "", "definition": "def"}]})

    def test_build_behavior_factor_returns_name_and_description_only(self) -> None:
        policy = {
            "behaviors": [
                {
                    "name": "B1",
                    "definition": "d1",
                    "permissible": False,
                    "examples": ["ex1", "ex2"],
                },
            ],
        }
        factor = build_behavior_factor(policy)
        self.assertEqual(factor[0], {"name": "B1", "description": "d1"})


if __name__ == "__main__":
    unittest.main()
