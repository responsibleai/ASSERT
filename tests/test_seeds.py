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
    build_failure_mode_factor,
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


def _make_taxonomy(
    failure_modes: list[tuple[str, str, bool]] | None = None,
) -> dict[str, object]:
    if failure_modes is None:
        failure_modes = [
            ("FailureMode A", "Definition of A", True),
            ("FailureMode B", "Definition of B", False),
        ]
    return {
        "spec": {"name": "test-spec"},
        "failure_modes": [
            {"name": name, "definition": definition, "permissible": permissible}
            for name, definition, permissible in failure_modes
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


def _make_design_with_failure_mode(levels: int = 3) -> dict[str, list[dict[str, str]]]:
    design = normalize_design(
        _make_factor_design(levels),
        _make_taxonomy(),
        factor_order=list(FACTOR_NAMES),
        inject_failure_mode=True,
    )
    return design


class NormalizeDesignTest(unittest.TestCase):
    def test_normalize_design_injects_failure_mode_when_requested(self) -> None:
        design = normalize_design(
            _make_factor_design(),
            _make_taxonomy(),
            factor_order=list(FACTOR_NAMES),
            inject_failure_mode=True,
        )
        self.assertEqual(list(design_factors(design)), list(FACTOR_NAMES))
        self.assertIn("failure_mode", design)

    def test_normalize_design_uses_design_failure_mode_subset(self) -> None:
        raw_design = {
            "failure_mode": [
                {"name": "FailureMode B", "description": "Definition of B"},
            ],
            **_make_factor_design(2),
        }
        design = normalize_design(raw_design, _make_taxonomy())
        self.assertEqual([entry["name"] for entry in design["failure_mode"]], ["FailureMode B"])

    def test_normalize_design_allows_custom_failure_mode_not_in_taxonomy(self) -> None:
        raw_design = {
            "failure_mode": [
                {"name": "custom_failure_mode", "description": "A brand-new probe"},
            ],
        }
        design = normalize_design(raw_design, _make_taxonomy())
        self.assertEqual([entry["name"] for entry in design["failure_mode"]], ["custom_failure_mode"])
        self.assertEqual(design["failure_mode"][0]["description"], "A brand-new probe")

    def test_normalize_design_preserves_diverging_description(self) -> None:
        raw_design = {
            "failure_mode": [
                {
                    "name": "FailureMode A",
                    "description": "A refined definition that differs from taxonomy",
                },
            ],
        }
        design = normalize_design(raw_design, _make_taxonomy())
        self.assertEqual(
            design["failure_mode"][0]["description"],
            "A refined definition that differs from taxonomy",
        )

    def test_normalize_design_rejects_factor_mismatch_against_configured_order(self) -> None:
        raw_design = {"domain": [{"name": "A", "definition": "B"}]}
        with self.assertRaises(ValueError):
            normalize_design(
                raw_design,
                _make_taxonomy(),
                factor_order=["domain", "user_context"],
                inject_failure_mode=True,
            )

    def test_normalize_design_accepts_failure_mode_absent_mode(self) -> None:
        design = normalize_design(_make_factor_design(), _make_taxonomy())
        self.assertNotIn("failure_mode", design)


class BuildGenerationJobsDisentangledTest(unittest.TestCase):
    def test_custom_failure_mode_without_taxonomy_entry_produces_jobs(self) -> None:
        taxonomy = _make_taxonomy()
        raw_design = {
            "failure_mode": [
                {
                    "name": "custom_probe",
                    "description": "A custom probe not in taxonomy",
                },
            ],
            **_make_factor_design(2),
        }
        design = normalize_design(raw_design, taxonomy)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy, design=design, sample_size=4, rng=random.Random(0),
        )
        self.assertTrue(jobs)
        for job in jobs:
            self.assertEqual(job.failure_mode["name"], "custom_probe")
            self.assertEqual(
                job.failure_mode["description"],
                "A custom probe not in taxonomy",
            )

    def test_renamed_description_is_used_verbatim(self) -> None:
        taxonomy = _make_taxonomy(failure_modes=[("FailureMode A", "taxonomy definition", True)])
        raw_design = {
            "failure_mode": [
                {
                    "name": "FailureMode A",
                    "description": "suite-specific refined definition",
                },
            ],
            **_make_factor_design(2),
        }
        design = normalize_design(raw_design, taxonomy)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy, design=design, sample_size=2, rng=random.Random(0),
        )
        self.assertEqual(
            jobs[0].failure_mode["description"],
            "suite-specific refined definition",
        )


class CoveringArrayTest(unittest.TestCase):
    def test_build_covering_array_covers_all_pair_cells(self) -> None:
        design = _make_design_with_failure_mode(3)
        ca = build_covering_array(design, random.Random(42), axes=FACTOR_NAMES)
        for factor_a, factor_b in combinations(FACTOR_NAMES, 2):
            observed = {(row[factor_a], row[factor_b]) for row in ca}
            possible = len(design[factor_a]) * len(design[factor_b])
            self.assertEqual(len(observed), possible)

    def test_sample_from_covering_array_returns_requested_count(self) -> None:
        design = _make_design_with_failure_mode(3)
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
        design = _make_design_with_failure_mode(2)
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
        design = _make_design_with_failure_mode(2)
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
            spec="test-spec",
            seed_payload={"title": "t", "description": "d"},
        )
        self.assertEqual(record["kind"], "prompt")
        self.assertEqual(record["seed_id"], "ps-001")
        self.assertNotIn("permissible", record)

    def test_seed_record_omits_empty_factors(self) -> None:
        record = seed_record(
            kind="prompt",
            seed_id="ps-001",
            spec="test-spec",
            seed_payload={"title": "t", "description": "d"},
            factors={},
        )
        self.assertNotIn("factors", record)

    def test_seed_record_persists_factors(self) -> None:
        record = seed_record(
            kind="prompt",
            seed_id="ps-001",
            spec="test-spec",
            seed_payload={"title": "t", "description": "d"},
            factors={"domain": "domain 0"},
        )
        self.assertEqual(record["factors"], {"domain": "domain 0"})


class BuildGenerationPromptTest(unittest.TestCase):
    def _make_args(self, **overrides: object) -> dict[str, object]:
        taxonomy = _make_taxonomy()
        design = _make_design_with_failure_mode(3)
        defaults = {
            "kind": "prompt",
            "taxonomy": taxonomy,
            "failure_mode": design["failure_mode"][0],
            "count": 4,
            "context": None,
            "design": design,
            "tuple_spec": {
                "domain": design["domain"][0],
                "user_context": design["user_context"][0],
                "failure_mode": design["failure_mode"][0],
            },
        }
        defaults.update(overrides)
        return defaults

    def test_build_generation_prompt_includes_assignments(self) -> None:
        prompt = build_generation_prompt(**self._make_args())
        self.assertIn("Exact Design Assignment", prompt)
        self.assertIn("FailureMode A", prompt)

    def test_build_generation_prompt_injects_taxonomy_body(self) -> None:
        taxonomy = _make_taxonomy(failure_modes=[("FailureMode A", "A-specific definition", True)])
        design = _make_design_with_failure_mode(3)
        # Override the failure_mode level so it matches the taxonomy.
        design["failure_mode"] = [{"name": "FailureMode A", "description": "A-specific definition"}]
        args = {
            "kind": "scenario",
            "taxonomy": taxonomy,
            "failure_mode": design["failure_mode"][0],
            "count": 2,
            "context": None,
            "design": design,
            "tuple_spec": {
                "domain": design["domain"][0],
                "user_context": design["user_context"][0],
                "failure_mode": design["failure_mode"][0],
            },
        }
        prompt = build_generation_prompt(**args)
        self.assertIn("Taxonomy context", prompt)
        self.assertIn("A-specific definition", prompt)
        self.assertNotIn("{{taxonomy_body}}", prompt)
        self.assertNotIn("permissible_status", prompt)
        self.assertNotIn("seed_strategy", prompt)


class BuildGenerationJobsTest(unittest.TestCase):
    def test_generation_jobs_cover_failure_mode_subset(self) -> None:
        taxonomy = _make_taxonomy()
        design = normalize_design(
            {
                "failure_mode": [{"name": "FailureMode B", "description": "Definition of B"}],
                **_make_factor_design(2),
            },
            taxonomy,
        )
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            design=design,
            sample_size=4,
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), 4)
        self.assertIsNotNone(assignments)
        self.assertEqual({row["failure_mode"] for row in assignments or []}, {"FailureMode B"})

    def test_generation_jobs_always_include_failure_mode_in_assignments(self) -> None:
        taxonomy = _make_taxonomy()
        design = normalize_design(_make_factor_design(2), taxonomy, inject_failure_mode=True)
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            design=design,
            sample_size=6,
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), 6)
        self.assertIsNotNone(assignments)
        self.assertTrue(all("failure_mode" in row for row in assignments or []))
        self.assertTrue(
            all(set(row) == {"failure_mode", *FACTOR_NAMES} for row in assignments or [])
        )

    def test_generation_jobs_with_failure_mode_only_design(self) -> None:
        taxonomy = _make_taxonomy()
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            design={"failure_mode": build_failure_mode_factor(taxonomy)},
            sample_size=3,
            rng=random.Random(0),
        )
        self.assertEqual(sum(job.count for job in jobs), 3)
        self.assertTrue(all(set(row) == {"failure_mode"} for row in assignments or []))

    def test_generation_jobs_one_job_per_tuple(self) -> None:
        """Each covering-array tuple gets its own job."""
        taxonomy = _make_taxonomy()
        design = _make_design_with_failure_mode(2)
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            design=design,
            sample_size=len(build_covering_array(design, random.Random(42), axes=("failure_mode",) + FACTOR_NAMES)),
            rng=random.Random(42),
        )
        self.assertEqual(sum(job.count for job in jobs), len(jobs))
        self.assertTrue(all(job.count == 1 for job in jobs))

    def test_generation_jobs_budget_allocation_divmod(self) -> None:
        """Budget spreads evenly with remainder going to first tuples."""
        taxonomy = _make_taxonomy()
        design = _make_design_with_failure_mode(2)
        ca = build_covering_array(design, random.Random(42), axes=("failure_mode",) + FACTOR_NAMES)
        num_tuples = len(ca)
        sample_size = num_tuples * 2 + 3
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
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
        taxonomy = _make_taxonomy()
        design = _make_design_with_failure_mode(2)
        sample_size = 3
        jobs, assignments = build_generation_jobs(
            taxonomy=taxonomy,
            design=design,
            sample_size=sample_size,
            rng=random.Random(42),
        )
        self.assertEqual(len(jobs), sample_size)
        self.assertTrue(all(job.count == 1 for job in jobs))
        self.assertEqual(sum(job.count for job in jobs), sample_size)

    def test_generation_jobs_start_index_contiguous(self) -> None:
        """start_index values are contiguous across all jobs."""
        taxonomy = _make_taxonomy()
        design = normalize_design(_make_factor_design(2), taxonomy, inject_failure_mode=True)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy,
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
        taxonomy = _make_taxonomy()
        design = normalize_design(_make_factor_design(2), taxonomy, inject_failure_mode=True)
        jobs, _ = build_generation_jobs(
            taxonomy=taxonomy,
            design=design,
            sample_size=6,
            rng=random.Random(42),
        )
        for job in jobs:
            self.assertIsInstance(job.tuple_spec, dict)
            self.assertIn("failure_mode", job.tuple_spec)
            for factor_name in FACTOR_NAMES:
                self.assertIn(factor_name, job.tuple_spec)


class BuildPolicyNodeFactorTest(unittest.TestCase):
    def test_build_failure_mode_factor_creates_entries_from_failure_modes(self) -> None:
        factor = build_failure_mode_factor(_make_taxonomy())
        self.assertEqual(
            [entry["name"] for entry in factor],
            ["FailureMode A", "FailureMode B"],
        )

    def test_build_failure_mode_factor_raises_on_missing_name(self) -> None:
        with self.assertRaises(ValueError):
            build_failure_mode_factor({"failure_modes": [{"name": "", "definition": "def"}]})

    def test_build_failure_mode_factor_returns_name_and_description_only(self) -> None:
        taxonomy = {
            "failure_modes": [
                {
                    "name": "B1",
                    "definition": "d1",
                    "permissible": False,
                    "examples": ["ex1", "ex2"],
                },
            ],
        }
        factor = build_failure_mode_factor(taxonomy)
        self.assertEqual(factor[0], {"name": "B1", "description": "d1"})


if __name__ == "__main__":
    unittest.main()
