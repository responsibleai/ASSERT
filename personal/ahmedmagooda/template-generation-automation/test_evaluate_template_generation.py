from __future__ import annotations

import importlib.util
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


MODULE_PATH = Path(__file__).with_name("evaluate_template_generation.py")
TEST_ENDPOINT = "https://example.openai.azure.com"
TEST_EMBEDDING_DEPLOYMENT = "embedding/deployment"
TEST_RUN_NAMES = ("run-1", "run-2", "run-3")
SPEC = importlib.util.spec_from_file_location("harm_template_analyze", MODULE_PATH)
assert SPEC and SPEC.loader
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class AzureMetricClientTests(unittest.TestCase):
    def test_embeddings_and_responses_use_requested_endpoints_and_payloads(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout, json.loads(request.data)))
            if "/embeddings?" in request.full_url:
                return FakeResponse(
                    {
                        "data": [
                            {"index": 1, "embedding": [0.0, 1.0]},
                            {"index": 0, "embedding": [1.0, 0.0]},
                        ]
                    }
                )
            return FakeResponse(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"results": []}),
                                }
                            ],
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"AZURE_API_KEY": "unit-test-placeholder"}, clear=False
        ):
            client = analyze.AzureMetricClient(
                "judge-deployment",
                analyze.JsonCache(Path(temp_dir) / "cache.json"),
                endpoint=TEST_ENDPOINT,
                embedding_deployment=TEST_EMBEDDING_DEPLOYMENT,
                auth_mode="key",
                opener=opener,
            )
            vectors = client.embeddings(["first", "second"], batch_size=2)
            result = client.grade_json(
                system_prompt=analyze.HARM_GRADER_SYSTEM_PROMPT,
                user_prompt="harm input",
                schema_name="test_harm",
                schema=analyze.HARM_GRADER_RESULT_SCHEMA,
            )

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(result, {"results": []})
        self.assertEqual(
            requests[0][0].full_url,
            "https://example.openai.azure.com/openai/deployments/"
            "embedding%2Fdeployment/embeddings?api-version=2024-12-01-preview",
        )
        self.assertEqual(requests[0][2], {"input": ["first", "second"]})
        self.assertEqual(
            requests[1][0].full_url,
            "https://example.openai.azure.com/openai/responses"
            "?api-version=2025-04-01-preview",
        )
        response_payload = requests[1][2]
        self.assertEqual(response_payload["model"], "judge-deployment")
        self.assertEqual(
            response_payload["input"][0]["content"][0]["text"],
            analyze.HARM_GRADER_SYSTEM_PROMPT,
        )
        self.assertEqual(response_payload["input"][1]["content"][0]["text"], "harm input")
        self.assertEqual(response_payload["text"]["format"]["type"], "json_schema")

    def test_cosine_similarity_and_response_output_text(self) -> None:
        self.assertAlmostEqual(analyze.cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(analyze.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(analyze.extract_response_text({"output_text": "ok"}), "ok")

    def test_raw_timeout_is_retried(self) -> None:
        attempts = 0

        def opener(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("read timed out")
            return FakeResponse({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"AZURE_API_KEY": "unit-test-placeholder"}, clear=False
        ), patch.object(analyze.time, "sleep"):
            client = analyze.AzureMetricClient(
                "judge-deployment",
                analyze.JsonCache(Path(temp_dir) / "cache.json"),
                endpoint=TEST_ENDPOINT,
                embedding_deployment=TEST_EMBEDDING_DEPLOYMENT,
                retries=1,
                opener=opener,
            )
            vectors = client.embeddings(["first"], batch_size=1)

        self.assertEqual(vectors, [[1.0, 0.0]])
        self.assertEqual(attempts, 2)

    def test_exhausted_timeout_has_actionable_error(self) -> None:
        def opener(request, timeout):
            raise TimeoutError("read timed out")

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"AZURE_API_KEY": "unit-test-placeholder"}, clear=False
        ), patch.object(analyze.time, "sleep"):
            client = analyze.AzureMetricClient(
                "judge-deployment",
                analyze.JsonCache(Path(temp_dir) / "cache.json"),
                endpoint=TEST_ENDPOINT,
                embedding_deployment=TEST_EMBEDDING_DEPLOYMENT,
                timeout=30,
                retries=1,
                opener=opener,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                r"timed out after 30s.*Increase --timeout",
            ):
                client.embeddings(["first"], batch_size=1)

    def test_explicit_aad_mode_overrides_present_api_key(self) -> None:
        tokens = iter(("aad-token-1", "aad-token-2"))
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"AZURE_API_KEY": "wrong-resource-placeholder"}, clear=False
        ), patch(
            "assert_ai.core.azure_auth.get_azure_token_provider",
            return_value=lambda: next(tokens),
        ) as token_provider:
            client = analyze.AzureMetricClient(
                "judge-deployment",
                analyze.JsonCache(Path(temp_dir) / "cache.json"),
                endpoint=TEST_ENDPOINT,
                embedding_deployment=TEST_EMBEDDING_DEPLOYMENT,
                auth_mode="aad",
                aad_scope="ai",
            )
            first_headers = client._auth_headers()
            second_headers = client._auth_headers()

        self.assertEqual(first_headers, {"Authorization": "Bearer aad-token-1"})
        self.assertEqual(second_headers, {"Authorization": "Bearer aad-token-2"})
        self.assertEqual(client._resolved_auth_mode, "aad")
        token_provider.assert_called_once_with(analyze.AZURE_AI_SCOPE)


class ExperimentLayoutTests(unittest.TestCase):
    def test_cumulative_run_endpoints_include_odd_checkpoints_and_final_run(self) -> None:
        self.assertEqual(analyze.cumulative_run_endpoints(1), (1,))
        self.assertEqual(analyze.cumulative_run_endpoints(3), (3,))
        self.assertEqual(analyze.cumulative_run_endpoints(7), (3, 5, 7))
        self.assertEqual(analyze.cumulative_run_endpoints(8), (3, 5, 7, 8))
        self.assertEqual(analyze.cumulative_run_endpoints(10), (3, 5, 7, 9, 10))

    def test_natural_run_order_places_run_ten_after_run_two(self) -> None:
        names = ["run-10", "run-2", "run-1"]

        self.assertEqual(
            sorted(names, key=analyze.natural_name_key),
            ["run-1", "run-2", "run-10"],
        )

    def test_cumulative_plan_is_independent_for_each_harm(self) -> None:
        plan = analyze.cumulative_run_plan(
            {
                "seven_runs": {f"run-{index}": index for index in range(1, 8)},
                "eight_runs": {f"run-{index}": index for index in range(1, 9)},
            }
        )

        self.assertEqual(
            [len(run_counts) for run_counts in plan["seven_runs"]],
            [3, 5, 7],
        )
        self.assertEqual(
            [len(run_counts) for run_counts in plan["eight_runs"]],
            [3, 5, 7, 8],
        )
        self.assertEqual(
            list(plan["eight_runs"][1]),
            ["run-1", "run-2", "run-3", "run-4", "run-5"],
        )

    def test_discovers_v1_and_v2_counts(self) -> None:
        v1 = analyze.resolve_experiment_root("templates-by-skill-v1")
        v2 = analyze.resolve_experiment_root("templates-by-skill-v2")

        v1_counts = analyze.discover_harm_run_counts(v1)
        v2_counts = analyze.discover_harm_run_counts(v2)
        for root, counts in ((v1, v1_counts), (v2, v2_counts)):
            self.assertEqual(
                set(counts),
                {
                    path.name
                    for path in root.iterdir()
                    if path.is_dir() and path.name != "analysis"
                },
            )
        self.assertEqual(
            v1_counts["violent_content"],
            {"run-1": 4, "run-2": 3, "run-3": 4},
        )
        self.assertEqual(
            v2_counts["violent_content"],
            {"run-1": 8, "run-2": 9, "run-3": 10},
        )

    def test_discovers_arbitrary_harms_and_runs_with_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "analysis").mkdir()
            for harm, runs in {"harm_b": ("attempt-z",), "harm_a": ("alpha", "beta")}.items():
                harm_dir = root / harm
                (harm_dir / "merged").mkdir(parents=True)
                for run in runs:
                    run_dir = harm_dir / run
                    run_dir.mkdir()
                    (run_dir / "eval_config.yaml").write_text(
                        yaml.safe_dump(
                            {
                                "pipeline": {
                                    "test_set": {
                                        "stratify": {
                                            "dimensions": [
                                                {"name": "first"},
                                                {"name": "second"},
                                            ]
                                        }
                                    }
                                }
                            }
                        ),
                        encoding="utf-8",
                    )

            self.assertEqual(
                analyze.discover_harm_run_counts(root),
                {
                    "harm_a": {"alpha": 2, "beta": 2},
                    "harm_b": {"attempt-z": 2},
                },
            )

    def test_loads_config_without_optional_support_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "example_harm" / "baseline"
            run_dir.mkdir(parents=True)
            (run_dir / "eval_config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "context": "Example evaluation context.",
                        "pipeline": {
                            "test_set": {
                                "stratify": {
                                    "dimensions": [
                                        {"name": "first", "description": "First axis."},
                                        {"name": "second", "description": "Second axis."},
                                    ]
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            rows = analyze.load_dimensions(
                root, {"example_harm": {"baseline": 2}}
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_config"], "example_harm/baseline/eval_config.yaml")
        self.assertEqual(rows[0]["source_ledger"], "")
        self.assertEqual(rows[0]["source_validation"], "")


class MetricsMetadataTests(unittest.TestCase):
    def test_root_metrics_record_all_prefix_requests_and_prompts(self) -> None:
        args = SimpleNamespace(
            experiment_dir="experiment",
            judge_model="judge",
            auth_mode="aad",
            aad_scope="cognitiveservices",
            embedding_deployment="embedding",
            llm_duplicate_threshold=0.9,
            embedding_duplicate_threshold=0.9,
            binary_relevance_threshold_percent=75,
        )
        client = SimpleNamespace(
            endpoint="https://example.openai.azure.com",
            embeddings_endpoint="https://example/embeddings",
            responses_endpoint="https://example/responses",
        )
        bundle = {
            "harm_run_counts": {"example_harm": {"run-1": 2, "run-2": 2}},
            "rows": [],
            "pairs": [],
            "unique_groups": [],
            "run_results": {},
            "harm_results": {},
            "global_unique_metrics": {},
            "llm_harm_grades": {},
            "llm_additional_harm_grades": {},
        }
        cumulative_results = {
            "example_harm": [
                {"run_count": run_count} for run_count in (3, 5, 7, 8)
            ]
        }

        metrics = analyze.build_metrics(
            args,
            client,
            bundle,
            {"example_harm": {"harm": "example_harm"}},
            cumulative_run_results=cumulative_results,
        )

        methodology = metrics["methodology"]
        self.assertEqual(methodology["llm_request_count"], 12)
        self.assertEqual(
            methodology["cumulative_run_prefixes"]["example_harm"],
            [3, 5, 7, 8],
        )
        self.assertEqual(
            methodology["prompts"]["harm_grader_system"],
            analyze.HARM_GRADER_SYSTEM_PROMPT,
        )
        self.assertIs(metrics["cumulative_run_results"], cumulative_results)

    def test_prefix_evaluation_writes_complete_artifact_set(self) -> None:
        run_names = ("run-1", "run-2", "run-3")
        dimension_ids = [
            f"violent_content/{run}/{index}"
            for run in run_names
            for index in (1, 2)
        ]

        class FakeSliceClient:
            endpoint = TEST_ENDPOINT
            embeddings_endpoint = f"{TEST_ENDPOINT}/embeddings"
            responses_endpoint = f"{TEST_ENDPOINT}/responses"

            def grade_json(self, **kwargs):
                schema = kwargs["schema"]
                if schema is analyze.HARM_GRADER_RESULT_SCHEMA:
                    return {
                        "dimension_results": [
                            {
                                "dimension_id": dimension_id,
                                "semantic_tags": [
                                    f"tag-{position}",
                                    "dimension",
                                    "test-axis",
                                ],
                                "canonical_concept": f"concept-{position}",
                                "concept_family": f"family-{position}",
                                "duplicate_group_id": f"group-{position}",
                                "relevance_score": 4,
                                "relevance_rationale": "Direct test axis.",
                            }
                            for position, dimension_id in enumerate(dimension_ids, 1)
                        ],
                        "mean_dimension_count": 2,
                        "population_variance_dimension_count": 0,
                        "sample_variance_dimension_count": 0,
                        "within_run_diversity": [
                            {"run": run, "score": 1, "rationale": "Distinct axes."}
                            for run in run_names
                        ],
                        "across_run_union_diversity": 1,
                        "across_run_rationale": "All axes are distinct.",
                        "variance_rationale": "Counts are identical.",
                    }
                if schema is analyze.ADVERSARIAL_GRADER_RESULT_SCHEMA:
                    return {
                        "dimension_results": [
                            {
                                "dimension_id": dimension_id,
                                "adversarial_score": 50,
                                "adversarial_rationale": "Moderate pressure.",
                            }
                            for dimension_id in dimension_ids
                        ]
                    }
                if schema is analyze.COVERAGE_GRADER_RESULT_SCHEMA:
                    return {
                        "coverage_score": 100,
                        "coverage_rationale": "Complete synthetic coverage.",
                        "weak_coverage_points": [],
                    }
                raise AssertionError("Unexpected schema")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "experiment"
            for run in run_names:
                run_dir = root / "violent_content" / run
                run_dir.mkdir(parents=True)
                (run_dir / "eval_config.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "context": "Synthetic context.",
                            "pipeline": {
                                "test_set": {
                                    "stratify": {
                                        "dimensions": [
                                            {
                                                "name": f"{run}_first",
                                                "description": "First axis.",
                                            },
                                            {
                                                "name": f"{run}_second",
                                                "description": "Second axis.",
                                            },
                                        ]
                                    }
                                }
                            },
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
            run_counts = {"violent_content": dict.fromkeys(run_names, 2)}
            embedding_vectors = {
                dimension_id: [
                    1.0 if vector_index == position else 0.0
                    for vector_index in range(len(dimension_ids))
                ]
                for position, dimension_id in enumerate(dimension_ids)
            }
            client = FakeSliceClient()
            bundle = analyze.evaluate_run_slice(
                client,
                root,
                run_counts,
                {"violent_content": {"harm": "violent_content"}},
                embedding_batch_size=64,
                embedding_vectors=embedding_vectors,
                llm_duplicate_threshold=0.9,
                embedding_duplicate_threshold=0.9,
                binary_relevance_threshold_percent=75,
            )
            args = SimpleNamespace(
                experiment_dir="experiment",
                judge_model="judge",
                auth_mode="aad",
                aad_scope="cognitiveservices",
                embedding_deployment="embedding",
                llm_duplicate_threshold=0.9,
                embedding_duplicate_threshold=0.9,
                binary_relevance_threshold_percent=75,
            )
            prefix_metadata = {
                "harm": "violent_content",
                "run_count": 3,
                "run_names": list(run_names),
                "is_final_prefix": True,
            }
            metrics = analyze.build_metrics(
                args,
                client,
                bundle,
                {"violent_content": {"harm": "violent_content"}},
                run_prefix=prefix_metadata,
            )
            output_dir = root / "analysis" / "run-prefixes" / "violent_content" / "runs-1-to-3"
            analyze.write_evaluation_artifacts(
                bundle,
                metrics,
                "synthetic prefix",
                output_dir,
                include_readme=False,
            )
            written_metrics = json.loads(
                (output_dir / "metrics.json").read_text(encoding="utf-8")
            )
            filenames = {path.name for path in output_dir.iterdir()}

        self.assertEqual(written_metrics["methodology"]["run_prefix"], prefix_metadata)
        self.assertEqual(written_metrics["totals"]["source_config_count"], 3)
        self.assertEqual(written_metrics["totals"]["dimension_count"], 6)
        self.assertTrue(
            {
                "metrics.json",
                "report.md",
                "dimension_inventory.csv",
                "pairwise_similarity.csv",
                "unique_dimension_groups.csv",
                "coverage_weak_points.csv",
                "coverage_dimension_suggestions.yaml",
            }
            <= filenames
        )


class CoordinatedHarmGraderTests(unittest.TestCase):
    def test_one_harm_call_derives_all_pair_metrics(self) -> None:
        run_names = ("baseline", "retry", "release-candidate")
        rows = []
        for index, run in enumerate(run_names, 1):
            row = {
                "dimension_id": f"violent_content/{run}/1",
                "harm": "violent_content",
                "run": run,
                "raw_name": f"dimension_{index}",
                "description": f"Description {index}",
                "levels_summary": "Generated levels",
                "evaluation_context": "Text-chat safety evaluation.",
            }
            row["embedding_text"] = analyze.dimension_text(row)
            row["normalized_name"] = analyze.normalized_text(row["raw_name"])
            row["normalized_dimension_text"] = analyze.normalized_text(row["embedding_text"])
            rows.append(row)
        run_counts = {"violent_content": dict.fromkeys(run_names, 1)}
        pairs = analyze.make_pairs(rows, run_counts)
        for pair in pairs:
            pair["embedding_similarity"] = 0.5
            pair["embedding_distance"] = 0.5

        class FakeGrader:
            calls = 0

            def grade_json(self, **kwargs):
                self.calls += 1
                return {
                    "dimension_results": [
                        {
                            "dimension_id": rows[0]["dimension_id"],
                            "semantic_tags": ["severity", "physical-harm", "intensity"],
                            "canonical_concept": "harm severity",
                            "concept_family": "harm manifestation",
                            "duplicate_group_id": "severity",
                            "relevance_score": 4,
                            "relevance_rationale": "Direct harm axis.",
                        },
                        {
                            "dimension_id": rows[1]["dimension_id"],
                            "semantic_tags": ["severity", "physical-harm", "intensity"],
                            "canonical_concept": "harm severity",
                            "concept_family": "harm manifestation",
                            "duplicate_group_id": "severity",
                            "relevance_score": 4,
                            "relevance_rationale": "Direct harm axis.",
                        },
                        {
                            "dimension_id": rows[2]["dimension_id"],
                            "semantic_tags": ["audience", "age", "population"],
                            "canonical_concept": "audience age",
                            "concept_family": "population context",
                            "duplicate_group_id": "audience-age",
                            "relevance_score": 3,
                            "relevance_rationale": "Material context axis.",
                        },
                    ],
                    "mean_dimension_count": 1,
                    "population_variance_dimension_count": 0,
                    "sample_variance_dimension_count": 0,
                    "within_run_diversity": [
                        {"run": run, "score": 1, "rationale": "Single dimension."}
                        for run in run_names
                    ],
                    "across_run_union_diversity": 0.6,
                    "across_run_rationale": "Two construct groups.",
                    "variance_rationale": "Counts are identical.",
                }

        client = FakeGrader()
        grades = analyze.grade_harms(
            client, rows, pairs, run_counts
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(grades["violent_content"]["across_run_union_diversity"], 0.6)
        duplicate_pair = next(
            pair for pair in pairs
            if {pair["left_id"], pair["right_id"]}
            == {rows[0]["dimension_id"], rows[1]["dimension_id"]}
        )
        self.assertTrue(duplicate_pair["llm_redundant"])
        self.assertEqual(duplicate_pair["llm_similarity"], 1.0)
        self.assertEqual(rows[2]["relevance_percent"], 75)


class HarmReferenceTests(unittest.TestCase):
    def test_loads_both_sources_and_merges_equivalent_descriptions(self) -> None:
        reference = analyze.load_harm_reference("violent_content")

        self.assertEqual(reference["harm"], "violent_content")
        self.assertEqual(
            reference["source_paths"],
            [
                "assert_ai/library/behaviors/violent_content.yaml",
                "examples/behavior_specs/violent_content.md",
            ],
        )
        self.assertIn("Detect threats, incitement", reference["library_summary"])
        self.assertEqual(len(reference["descriptions"]), 1)
        self.assertEqual(
            reference["descriptions"][0]["source_paths"],
            reference["source_paths"],
        )
        self.assertIn(
            "Operational support for violence",
            reference["descriptions"][0]["text"],
        )

    def test_all_experiment_harms_resolve_both_reference_sources(self) -> None:
        experiment_root = analyze.resolve_experiment_root("templates-by-skill-v2")
        for harm in analyze.discover_harm_run_counts(experiment_root):
            with self.subTest(harm=harm):
                reference = analyze.load_harm_reference(harm)
                self.assertEqual(
                    reference["source_paths"],
                    [
                        f"assert_ai/library/behaviors/{harm}.yaml",
                        f"examples/behavior_specs/{harm}.md",
                    ],
                )
                self.assertTrue(reference["library_summary"])
                self.assertEqual(len(reference["descriptions"]), 1)


class AdditionalHarmGraderTests(unittest.TestCase):
    def test_dedicated_prompts_attach_scores_and_return_coverage(self) -> None:
        rows = [
            {
                "dimension_id": f"violent_content/{run}/1",
                "harm": "violent_content",
                "run": run,
                "raw_name": f"dimension_{index}",
                "description": f"Description {index}",
                "levels_summary": "Generated levels",
                "evaluation_context": "Text-chat safety evaluation.",
            }
            for index, run in enumerate(TEST_RUN_NAMES, 1)
        ]

        class FakeGrader:
            def __init__(self) -> None:
                self.calls = []

            def grade_json(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs["schema"] is analyze.ADVERSARIAL_GRADER_RESULT_SCHEMA:
                    scores = (85, 75, 30)
                    return {
                        "dimension_results": [
                            {
                                "dimension_id": row["dimension_id"],
                                "adversarial_score": score,
                                "adversarial_rationale": f"Pressure score {score}.",
                            }
                            for row, score in zip(rows, scores, strict=True)
                        ]
                    }
                if kwargs["schema"] is analyze.COVERAGE_GRADER_RESULT_SCHEMA:
                    return {
                        "coverage_score": 65,
                        "coverage_rationale": (
                            "Core severity is covered, but attack breadth is limited."
                        ),
                        "weak_coverage_points": [
                            {
                                "gap_id": "violence_manifestation_type",
                                "gap_type": "missing_scenario_family",
                                "priority": "high",
                                "weak_coverage_point": (
                                    "Threats, incitement, and glorification are not "
                                    "independently expressible."
                                ),
                                "canonical_basis": (
                                    "Canonical non-permissible violent-content families."
                                ),
                                "current_coverage_evidence": (
                                    "Existing severity dimensions combine these families."
                                ),
                                "why_it_matters": (
                                    "Each family has a distinct safety boundary."
                                ),
                                "suggested_dimension": {
                                    "name": "violence_manifestation_type",
                                    "description": (
                                        "The canonical violent-content manifestation "
                                        "tested by the scenario."
                                    ),
                                    "levels": [
                                        {
                                            "name": "threat",
                                            "definition": "A direct or conditional threat.",
                                        },
                                        {
                                            "name": "incitement",
                                            "definition": "A call encouraging violence.",
                                        },
                                        {
                                            "name": "glorification",
                                            "definition": "Praise of a violent act.",
                                        },
                                    ],
                                },
                            }
                        ],
                    }
                raise AssertionError("Unexpected grader schema")

        client = FakeGrader()
        harm_reference = analyze.load_harm_reference("violent_content")
        grades = analyze.grade_adversariality_and_coverage(
            client,
            rows,
            {"violent_content": dict.fromkeys(TEST_RUN_NAMES, 1)},
            {"violent_content": harm_reference},
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            client.calls[0]["system_prompt"],
            analyze.ADVERSARIAL_GRADER_SYSTEM_PROMPT,
        )
        self.assertEqual(
            client.calls[1]["system_prompt"],
            analyze.COVERAGE_GRADER_SYSTEM_PROMPT,
        )
        for call in client.calls:
            self.assertIn(
                "assert_ai/library/behaviors/violent_content.yaml",
                call["user_prompt"],
            )
            self.assertIn(
                "examples/behavior_specs/violent_content.md",
                call["user_prompt"],
            )
            self.assertIn("Operational support for violence", call["user_prompt"])
        self.assertEqual(rows[0]["adversarial_score"], 85)
        self.assertEqual(rows[0]["adversarial_reason"], "Pressure score 85.")
        self.assertEqual(
            grades["violent_content"]["coverage"]["coverage_score"], 65
        )
        weak_point = grades["violent_content"]["coverage"][
            "weak_coverage_points"
        ][0]
        self.assertEqual(weak_point["priority"], "high")
        self.assertEqual(
            weak_point["suggested_dimension"]["name"],
            "violence_manifestation_type",
        )
        self.assertEqual(len(weak_point["suggested_dimension"]["levels"]), 3)

    def test_rejects_incomplete_coverage_gap_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "without weak points"):
            analyze.normalize_coverage_weak_points(
                {"coverage_score": 87, "weak_coverage_points": []},
                [{"raw_name": "existing_dimension"}],
                "violent_content",
            )


class AdversarialMetricTests(unittest.TestCase):
    def test_summary_reports_mean_variance_and_all_score_bins(self) -> None:
        rows = [
            {"adversarial_score": score}
            for score in (10, 30, 50, 70, 90)
        ]

        result = analyze.adversarial_summary(rows)

        self.assertEqual(result["mean"], 50.0)
        self.assertEqual(result["population_variance"], 800.0)
        self.assertEqual(result["sample_variance"], 1000.0)
        self.assertEqual(result["minimum"], 10)
        self.assertEqual(result["maximum"], 90)
        self.assertEqual(
            [(item["range"], item["count"], item["ratio"]) for item in result["distribution"]],
            [
                ("0-20", 1, 0.2),
                ("21-40", 1, 0.2),
                ("41-60", 1, 0.2),
                ("61-80", 1, 0.2),
                ("81-100", 1, 0.2),
            ],
        )

    def test_harm_summary_includes_adversarial_metrics_and_coverage(self) -> None:
        rows = []
        for run_number, run in enumerate(TEST_RUN_NAMES, 1):
            for index, adversarial_score in enumerate((20, 80), 1):
                rows.append(
                    {
                        "dimension_id": f"violent_content/{run}/{index}",
                        "harm": "violent_content",
                        "run": run,
                        "run_number": run_number,
                        "index": index,
                        "raw_name": f"dimension_{run_number}_{index}",
                        "normalized_name": f"dimension {run_number} {index}",
                        "normalized_dimension_text": (
                            f"dimension {run_number} {index} synthetic definition"
                        ),
                        "embedding_text": (
                            f"dimension {run_number} {index} synthetic definition"
                        ),
                        "relevance_score": 4,
                        "adversarial_score": adversarial_score,
                    }
                )
        run_counts = {"violent_content": dict.fromkeys(TEST_RUN_NAMES, 2)}
        pairs = analyze.make_pairs(rows, run_counts)
        for pair in pairs:
            pair.update(
                {
                    "llm_distance": 0.5,
                    "embedding_distance": 0.4,
                    "combined_distance": 0.45,
                    "llm_redundant": False,
                }
            )
        harm_grades = {
            "violent_content": {
                "mean_dimension_count": 2,
                "population_variance_dimension_count": 0,
                "sample_variance_dimension_count": 0,
                "variance_rationale": "Counts are identical.",
                "within_run_diversity": {
                    run: {"score": 0.5, "rationale": "Two distinct axes."}
                    for run in TEST_RUN_NAMES
                },
                "across_run_union_diversity": 0.5,
                "across_run_rationale": "Moderate union diversity.",
            }
        }
        additional_grades = {
            "violent_content": {
                "coverage": {
                    "coverage_score": 70,
                    "coverage_rationale": "Broad coverage with one material gap.",
                    "weak_coverage_points": [
                        {
                            "gap_id": "response_obligation",
                            "gap_type": "missing_response_obligation",
                            "priority": "high",
                            "weak_coverage_point": "Safe responses are not separated.",
                            "canonical_basis": "Canonical response obligations.",
                            "current_coverage_evidence": "No response-mode axis exists.",
                            "why_it_matters": "The model must choose the right response.",
                            "suggested_dimension": {
                                "name": "required_safe_response",
                                "description": "The safe response obligation.",
                                "levels": [
                                    {"name": "refuse", "definition": "Refuse help."},
                                    {"name": "redirect", "definition": "Redirect safely."},
                                ],
                            },
                        }
                    ],
                }
            }
        }

        _, harm_results = analyze.summarize(
            rows,
            pairs,
            harm_grades,
            additional_grades,
            run_counts,
        )

        result = harm_results["violent_content"]
        self.assertEqual(result["adversarial"]["mean"], 50.0)
        self.assertEqual(result["adversarial"]["population_variance"], 900.0)
        self.assertEqual(result["adversarial"]["sample_variance"], 1080.0)
        self.assertEqual(result["coverage"]["score"], 70)
        self.assertEqual(
            result["coverage"]["rationale"],
            "Broad coverage with one material gap.",
        )
        self.assertEqual(
            result["coverage"]["weak_coverage_points"][0]["gap_id"],
            "response_obligation",
        )


class ReportOutputTests(unittest.TestCase):
    def test_report_includes_adversarial_distribution_and_coverage(self) -> None:
        harm_results = {
            "violent_content": {
                "run_dimension_counts": [1, 1, 1],
                "average_dimension_count": 1.0,
                "population_variance_dimension_count": 0.0,
                "sample_variance_dimension_count": 0.0,
                "union_embedding_pairwise_diversity": 0.4,
                "union_llm_pairwise_diversity": 0.5,
                "union_llm_direct_diversity": 0.6,
                "relevance": {"mean": 3.5, "minimum": 3},
                "adversarial": {
                    "mean": 50.0,
                    "population_variance": 800.0,
                    "sample_variance": 1000.0,
                    "minimum": 10,
                    "maximum": 90,
                    "distribution": [
                        {
                            "range": f"{minimum}-{maximum}",
                            "count": 1,
                            "ratio": 0.2,
                        }
                        for minimum, maximum in analyze.ADVERSARIAL_SCORE_BINS
                    ],
                },
                "coverage": {
                    "score": 65,
                    "rationale": "Covers severity | misses multi-turn pressure.",
                    "weak_coverage_points": [
                        {
                            "gap_id": "multi_turn_pressure",
                            "gap_type": "missing_interaction",
                            "priority": "high",
                            "weak_coverage_point": "Adaptive pressure is absent.",
                            "canonical_basis": "Robust handling across interactions.",
                            "current_coverage_evidence": "Only single-turn axes exist.",
                            "why_it_matters": "Boundaries can erode over turns.",
                            "suggested_dimension": {
                                "name": "multi_turn_pressure",
                                "description": "Pressure applied after a safe response.",
                                "levels": [
                                    {"name": "none", "definition": "No follow-up."},
                                    {"name": "reframe", "definition": "Benign reframing."},
                                ],
                            },
                        }
                    ],
                },
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            analyze.write_report({}, harm_results, {}, "test-experiment", output_dir)
            report = (output_dir / "report.md").read_text(encoding="utf-8")

        self.assertIn("## Expected adversarial pressure", report)
        self.assertIn("| violent_content | 50.0000 | 800.0000 | 1000.0000 | 10 | 90 |", report)
        self.assertIn("| violent_content | 81-100 | 1 | 0.2000 |", report)
        self.assertIn("## Harm scenario-space coverage", report)
        self.assertIn("## Weak coverage points and suggested dimensions", report)
        self.assertIn("multi_turn_pressure", report)
        self.assertIn("Pressure applied after a safe response.", report)
        self.assertIn(
            "| violent_content | 65 | Covers severity \\| misses multi-turn pressure. |",
            report,
        )

    def test_writes_coverage_gap_csv_and_dimension_suggestions_yaml(self) -> None:
        weak_point = {
            "gap_id": "multi_turn_pressure",
            "gap_type": "missing_interaction",
            "priority": "high",
            "weak_coverage_point": "Adaptive pressure is absent.",
            "canonical_basis": "Robust handling across interactions.",
            "current_coverage_evidence": "Only single-turn axes exist.",
            "why_it_matters": "Boundaries can erode over turns.",
            "suggested_dimension": {
                "name": "multi_turn_pressure",
                "description": "Pressure applied after a safe response.",
                "levels": [
                    {"name": "none", "definition": "No follow-up."},
                    {"name": "reframe", "definition": "Benign reframing."},
                ],
            },
        }
        harm_results = {
            "violent_content": {
                "coverage": {"weak_coverage_points": [weak_point]}
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            analyze.write_csvs(
                [], [], [], harm_results, "test-experiment", output_dir
            )
            with (output_dir / "coverage_weak_points.csv").open(
                encoding="utf-8"
            ) as handle:
                csv_rows = list(csv.DictReader(handle))
            suggestions = yaml.safe_load(
                (output_dir / "coverage_dimension_suggestions.yaml").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(csv_rows[0]["gap_id"], "multi_turn_pressure")
        self.assertEqual(
            csv_rows[0]["suggested_dimension_name"], "multi_turn_pressure"
        )
        suggestion = suggestions["coverage_dimension_suggestions"][0]
        self.assertEqual(suggestion["priority"], "high")
        self.assertEqual(suggestion["dimension"]["levels"][1]["name"], "reframe")


class GlobalUniqueMetricTests(unittest.TestCase):
    def test_groups_highly_correlated_dimensions_and_builds_relevance_curve(self) -> None:
        rows = [
            {
                "dimension_id": "violent_content/run-1/1",
                "harm": "violent_content",
                "run": "run-1",
                "run_number": 1,
                "index": 1,
                "raw_name": "severity",
                "relevance_score": 4,
                "relevance_reason": "Directly measures violent harm severity.",
            },
            {
                "dimension_id": "violent_content/run-2/1",
                "harm": "violent_content",
                "run": "run-2",
                "run_number": 2,
                "index": 1,
                "raw_name": "harm_intensity",
                "relevance_score": 4,
                "relevance_reason": "Directly measures violent harm intensity.",
            },
            {
                "dimension_id": "violent_content/run-3/1",
                "harm": "violent_content",
                "run": "run-3",
                "run_number": 3,
                "index": 1,
                "raw_name": "locale",
                "relevance_score": 2,
                "relevance_reason": "Locale is only indirectly relevant here.",
            },
        ]
        pairs = [
            {
                "left_id": rows[0]["dimension_id"],
                "right_id": rows[1]["dimension_id"],
                "harm": "violent_content",
                "exact_name_match": False,
                "exact_dimension_match": False,
                "llm_redundant": False,
                "llm_similarity": 0.95,
                "embedding_similarity": 0.91,
                "combined_similarity": 0.93,
            },
            {
                "left_id": rows[0]["dimension_id"],
                "right_id": rows[2]["dimension_id"],
                "harm": "violent_content",
                "exact_name_match": False,
                "exact_dimension_match": False,
                "llm_redundant": False,
                "llm_similarity": 0.1,
                "embedding_similarity": 0.2,
                "combined_similarity": 0.15,
            },
            {
                "left_id": rows[1]["dimension_id"],
                "right_id": rows[2]["dimension_id"],
                "harm": "violent_content",
                "exact_name_match": False,
                "exact_dimension_match": False,
                "llm_redundant": False,
                "llm_similarity": 0.2,
                "embedding_similarity": 0.2,
                "combined_similarity": 0.2,
            },
        ]

        analyze.mark_deduplication_matches(
            pairs, llm_threshold=0.9, embedding_threshold=0.9
        )
        metrics, groups = analyze.unique_dimension_metrics(
            rows,
            pairs,
            {"violent_content": dict.fromkeys(TEST_RUN_NAMES, 1)},
            binary_relevance_threshold_percent=75,
        )

        result = metrics["violent_content"]
        self.assertEqual(result["total_dimension_count"], 3)
        self.assertEqual(result["repeated_dimension_count_removed"], 1)
        self.assertEqual(result["unique_dimension_count"], 2)
        self.assertAlmostEqual(result["unique_over_total_ratio"], 2 / 3, places=6)
        self.assertEqual(result["relevant_unique_dimension_count"], 1)
        self.assertEqual(result["relevant_unique_over_unique_ratio"], 0.5)
        threshold_50 = next(
            item for item in result["relevance_threshold_metrics"]
            if item["threshold_percent"] == 50
        )
        self.assertEqual(threshold_50["exact_score_count"], 1)
        self.assertEqual(threshold_50["at_or_above_count"], 2)
        self.assertEqual(sorted(group["member_count"] for group in groups), [1, 2])
        self.assertTrue(pairs[0]["deduplication_match"])


if __name__ == "__main__":
    unittest.main()