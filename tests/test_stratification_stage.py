# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the stratification pipeline stage."""

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from assert_ai.core.model_client import ModelResponse
from assert_ai.stages.stratification import DEFAULT_LEVEL_COUNT, SCOPE, SUITE_OUTPUT


class StratificationStageRegistrationTest(unittest.TestCase):
    def test_scope_is_suite(self):
        self.assertEqual(SCOPE, "suite")

    def test_suite_output_is_stratification_json(self):
        self.assertEqual(SUITE_OUTPUT, "stratification.json")

    def test_default_levels(self):
        self.assertEqual(DEFAULT_LEVEL_COUNT, 3)


class StratificationStageOrderingTest(unittest.TestCase):
    def test_stratification_is_internal_to_test_set(self):
        from assert_ai.config import PIPELINE_STAGE_ORDER

        self.assertEqual(PIPELINE_STAGE_ORDER, ("systematize", "test_set", "inference", "judge"))
        self.assertNotIn("stratification", PIPELINE_STAGE_ORDER)


class StratificationStageRegisteredTest(unittest.TestCase):
    def test_stratification_not_registered_as_pipeline_stage(self):
        from assert_ai.stages import STAGES

        self.assertNotIn("stratification", STAGES)


class StratificationStageConfigValidationTest(unittest.TestCase):
    def test_run_rejects_missing_model_when_factors_need_generation(self):
        from assert_ai.stages.stratification import run

        ctx = {
            "suite_root": Path("/tmp/test_suite"),
            "config_path": Path("/tmp/test.yaml"),
            "artifacts_root": Path("/tmp"),
            "stages": [],
            "dimensions": [{"name": "tone", "description": "How the user phrases the request."}],
        }
        with self.assertRaises(ValueError) as cm:
            asyncio.run(run(ctx, {}))
        self.assertIn("stratification.model is required", str(cm.exception))

    def test_run_rejects_invalid_levels(self):
        from assert_ai.stages.stratification import run

        ctx = {
            "suite_root": "/tmp/test_suite",
            "config_path": "/tmp/test.yaml",
            "artifacts_root": "/tmp",
            "stages": [],
        }
        raw_cfg = {
            "model": {"name": "test-model"},
            "level_count": 0,
        }
        with self.assertRaises(ValueError) as cm:
            asyncio.run(run(ctx, raw_cfg))
        self.assertIn("positive integer", str(cm.exception))

    def test_run_allows_missing_model_when_levels_are_preprovided(self):
        from assert_ai.stages.stratification import run

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            suite_root = root / "suite"
            config_path = root / "config.yaml"
            config_path.write_text("suite: demo\n", encoding="utf-8")
            ctx = {
                "suite_root": str(suite_root),
                "config_path": config_path,
                "artifacts_root": root,
                "stages": [],
                "dimensions": [
                    {
                        "name": "tone",
                        "description": "How the user phrases the request.",
                        "levels": [
                            {"name": "Neutral", "definition": "Neutral wording."},
                            {"name": "Urgent", "definition": "Urgent wording."},
                        ],
                    }
                ],
            }

            async def fake_run_stratification(*, model=None, **kwargs):
                self.assertIsNone(model)
                return {"stratification_path": str(Path(kwargs["out_dir"]) / "stratification.json")}

            with patch("assert_ai.stages.stratification.run_stratification", new=fake_run_stratification):
                result = asyncio.run(run(ctx, {}))

        self.assertEqual(
            Path(result["stratification_path"]).resolve(),
            (suite_root / "stratification.json").resolve(),
        )

    def test_run_resolves_save_dir_under_artifacts_root(self):
        from assert_ai.stages.stratification import run

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_dir = root / "configs"
            cfg_dir.mkdir()
            suite_root = root / "artifacts" / "results" / "suite-a"
            ctx = {
                "suite_root": str(suite_root),
                "config_path": cfg_dir / "stratification.yaml",
                "artifacts_root": root / "artifacts",
                "stages": [],
                "context": "A coding agent with shell access.",
                "dimensions": [
                    {
                        "name": "tone",
                        "description": "How the request is phrased.",
                        "levels": [
                            {"name": "Neutral", "definition": "Neutral wording."},
                            {"name": "Urgent", "definition": "Urgent wording."},
                        ],
                    }
                ],
            }

            async def fake_run_stratification(*, taxonomy_path, out_dir, dimensions=None, context=None, model=None, level_count, reasoning_effort=None, temperature=None):
                self.assertEqual(Path(taxonomy_path).resolve(), (suite_root / "taxonomy.json").resolve())
                self.assertEqual(model, "test-model")
                self.assertEqual(context, "A coding agent with shell access.")
                self.assertEqual(Path(out_dir).resolve(), (root / "artifacts" / "custom-output").resolve())
                self.assertEqual(level_count, DEFAULT_LEVEL_COUNT)
                self.assertEqual(dimensions[0]["name"], "tone")
                return {"stratification_path": str(Path(out_dir) / "stratification.json")}

            with patch("assert_ai.stages.stratification.run_stratification", new=fake_run_stratification):
                result = asyncio.run(
                    run(
                        ctx,
                        {
                            "model": {"name": "test-model"},
                            "save_dir": "custom-output",
                        },
                    )
                )

        self.assertEqual(
            Path(result["stratification_path"]).resolve(),
            (root / "artifacts" / "custom-output" / "stratification.json").resolve(),
        )


class RunStratificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_stratification_skips_llm_for_behavior_only(self) -> None:
        from assert_ai.stages.stratification import run_stratification

        taxonomy_payload = {
            "behavior": {"name": "Risk"},
            "behavior_categories": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            taxonomy_path = root / "taxonomy.json"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            generate_mock = AsyncMock()
            with patch("assert_ai.stages.stratification.generate_structured", generate_mock):
                result = await run_stratification(
                    taxonomy_path=str(taxonomy_path),
                    out_dir=str(root),
                    dimensions=[],
                    context="  Demo app  ",
                )

            payload = json.loads((root / "stratification.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {})
        self.assertEqual(payload["_context"], "Demo app")
        self.assertEqual(
            payload["behavior"][0],
            {"name": "Behavior A", "description": "Definition A"},
        )
        generate_mock.assert_not_awaited()

    async def test_run_stratification_merges_provided_and_generated_levels(self) -> None:
        from assert_ai.stages.stratification import run_stratification

        taxonomy_payload = {
            "behavior": {"name": "Risk"},
            "behavior_categories": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del prompt, options
            self.assertEqual(model, "azure/gpt-5.4")
            self.assertEqual(schema_name, "policy_stratification")
            self.assertEqual(set(json_schema["required"]), {"audience"})
            return ModelResponse(
                parsed={
                    "audience": [
                        {"name": "Beginner", "definition": "Little prior knowledge."},
                        {"name": "Expert", "definition": "Deep prior knowledge."},
                    ]
                },
                text="{}",
                model=model,
            )

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            taxonomy_path = root / "taxonomy.json"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("assert_ai.stages.stratification.generate_structured", new=fake_generate_structured):
                result = await run_stratification(
                    taxonomy_path=str(taxonomy_path),
                    out_dir=str(root),
                    model="azure/gpt-5.4",
                    dimensions=[
                        {
                            "name": "tone",
                            "description": "How the user phrases the request.",
                            "levels": [
                                {"name": "Neutral", "definition": "Neutral wording."},
                                {"name": "Urgent", "definition": "Urgent wording."},
                            ],
                        },
                        {
                            "name": "audience",
                            "description": "Who the request is aimed at.",
                        },
                    ],
                )

            payload = json.loads((root / "stratification.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {"tone": 2, "audience": 2})
        self.assertEqual(payload["tone"][0]["name"], "Neutral")
        self.assertEqual(payload["audience"][1]["name"], "Expert")

    async def test_run_stratification_explicit_levels_without_description(self) -> None:
        """Dimensions with explicit levels should not require description."""
        from assert_ai.stages.stratification import run_stratification

        taxonomy_payload = {
            "behavior": {"name": "Risk"},
            "behavior_categories": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            taxonomy_path = root / "taxonomy.json"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            generate_mock = AsyncMock()
            with patch("assert_ai.stages.stratification.generate_structured", generate_mock):
                result = await run_stratification(
                    taxonomy_path=str(taxonomy_path),
                    out_dir=str(root),
                    dimensions=[
                        {
                            "name": "tone",
                            "levels": [
                                {"name": "Neutral", "definition": "Neutral wording."},
                                {"name": "Urgent", "definition": "Urgent wording."},
                            ],
                        },
                    ],
                )

            payload = json.loads((root / "stratification.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {"tone": 2})
        self.assertEqual(payload["tone"][0]["name"], "Neutral")
        generate_mock.assert_not_awaited()

    async def test_run_stratification_rejects_generated_factor_without_description(self) -> None:
        """Dimensions without levels must provide a description."""
        from assert_ai.stages.stratification import run_stratification

        taxonomy_payload = {
            "behavior": {"name": "Risk"},
            "behavior_categories": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            taxonomy_path = root / "taxonomy.json"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with self.assertRaises(ValueError) as cm:
                await run_stratification(
                    taxonomy_path=str(taxonomy_path),
                    out_dir=str(root),
                    model="azure/gpt-5.4",
                    dimensions=[{"name": "tone"}],
                )

        self.assertIn("description is required", str(cm.exception))

    async def test_run_stratification_rejects_empty_levels(self) -> None:
        """Dimensions with levels: [] should be rejected."""
        from assert_ai.stages.stratification import run_stratification

        taxonomy_payload = {
            "behavior": {"name": "Risk"},
            "behavior_categories": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            taxonomy_path = root / "taxonomy.json"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with self.assertRaises(ValueError) as cm:
                await run_stratification(
                    taxonomy_path=str(taxonomy_path),
                    out_dir=str(root),
                    dimensions=[{"name": "tone", "levels": []}],
                )

        self.assertIn("must not be empty", str(cm.exception))

    async def test_run_stratification_mixed_explicit_no_desc_and_generated(self) -> None:
        """Mixed: one dimension with explicit levels (no description), one generated."""
        from assert_ai.stages.stratification import run_stratification

        taxonomy_payload = {
            "behavior": {"name": "Risk"},
            "behavior_categories": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            return ModelResponse(
                parsed={
                    "audience": [
                        {"name": "Beginner", "definition": "Little prior knowledge."},
                        {"name": "Expert", "definition": "Deep prior knowledge."},
                    ]
                },
                text="{}",
                model=model,
            )

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            taxonomy_path = root / "taxonomy.json"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("assert_ai.stages.stratification.generate_structured", new=fake_generate_structured):
                result = await run_stratification(
                    taxonomy_path=str(taxonomy_path),
                    out_dir=str(root),
                    model="azure/gpt-5.4",
                    dimensions=[
                        {
                            "name": "tone",
                            "levels": [
                                {"name": "Neutral", "definition": "Neutral wording."},
                                {"name": "Urgent", "definition": "Urgent wording."},
                            ],
                        },
                        {
                            "name": "audience",
                            "description": "Who the request is aimed at.",
                        },
                    ],
                )

            payload = json.loads((root / "stratification.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {"tone": 2, "audience": 2})
        self.assertEqual(payload["tone"][0]["name"], "Neutral")
        self.assertEqual(payload["audience"][1]["name"], "Expert")
