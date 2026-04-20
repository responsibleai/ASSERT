"""Tests for the design pipeline stage."""

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from p2m.core.model_client import ModelResponse
from p2m.stages.design import DEFAULT_LEVEL_COUNT, SCOPE, SUITE_OUTPUT


class DesignStageRegistrationTest(unittest.TestCase):
    def test_scope_is_suite(self):
        self.assertEqual(SCOPE, "suite")

    def test_suite_output_is_design_json(self):
        self.assertEqual(SUITE_OUTPUT, "design.json")

    def test_default_levels(self):
        self.assertEqual(DEFAULT_LEVEL_COUNT, 3)


class DesignStageOrderingTest(unittest.TestCase):
    def test_design_is_between_policy_and_seeds(self):
        from p2m.config import PIPELINE_STAGE_ORDER

        order = list(PIPELINE_STAGE_ORDER)
        self.assertIn("design", order)
        self.assertLess(order.index("policy"), order.index("design"))
        self.assertLess(order.index("design"), order.index("seeds"))

    def test_design_in_standard_pipeline_stages(self):
        from p2m.config import PIPELINE_STAGE_ORDER

        self.assertIn("design", PIPELINE_STAGE_ORDER)


class DesignStageRegisteredTest(unittest.TestCase):
    def test_design_registered_in_stages(self):
        from p2m.stages import STAGES

        self.assertIn("design", STAGES)


class DesignStageConfigValidationTest(unittest.TestCase):
    def test_run_rejects_missing_model_when_factors_need_generation(self):
        from p2m.stages.design import run

        ctx = {
            "suite_root": Path("/tmp/test_suite"),
            "config_path": Path("/tmp/test.yaml"),
            "artifacts_root": Path("/tmp"),
            "stages": [],
            "factors": [{"name": "tone", "description": "How the user phrases the request."}],
        }
        with self.assertRaises(ValueError) as cm:
            asyncio.run(run(ctx, {}))
        self.assertIn("design.model is required", str(cm.exception))

    def test_run_rejects_invalid_levels(self):
        from p2m.stages.design import run

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
        from p2m.stages.design import run

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
                "factors": [
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

            async def fake_run_design(*, model=None, **kwargs):
                self.assertIsNone(model)
                return {"design_path": str(Path(kwargs["out_dir"]) / "design.json")}

            with patch("p2m.stages.design.run_design", new=fake_run_design):
                result = asyncio.run(run(ctx, {}))

        self.assertEqual(result["design_path"], str(suite_root / "design.json"))

    def test_run_resolves_save_dir_under_artifacts_root(self):
        from p2m.stages.design import run

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_dir = root / "configs"
            cfg_dir.mkdir()
            suite_root = root / "artifacts" / "results" / "suite-a"
            ctx = {
                "suite_root": str(suite_root),
                "config_path": cfg_dir / "design.yaml",
                "artifacts_root": root / "artifacts",
                "stages": [],
                "context": "A coding agent with shell access.",
                "factors": [
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

            async def fake_run_design(*, policy_path, out_dir, factors=None, context=None, model=None, level_count, reasoning_effort=None, temperature=None):
                self.assertEqual(policy_path, str(suite_root / "policy.json"))
                self.assertEqual(model, "test-model")
                self.assertEqual(context, "A coding agent with shell access.")
                self.assertEqual(out_dir, str(root / "artifacts" / "custom-output"))
                self.assertEqual(level_count, DEFAULT_LEVEL_COUNT)
                self.assertEqual(factors[0]["name"], "tone")
                return {"design_path": str(Path(out_dir) / "design.json")}

            with patch("p2m.stages.design.run_design", new=fake_run_design):
                result = asyncio.run(
                    run(
                        ctx,
                        {
                            "model": {"name": "test-model"},
                            "save_dir": "custom-output",
                        },
                    )
                )

        self.assertEqual(result["design_path"], str(root / "artifacts" / "custom-output" / "design.json"))


class RunDesignTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_design_skips_llm_for_behavior_only(self) -> None:
        from p2m.stages.design import run_design

        policy_payload = {
            "concept": {"name": "Risk"},
            "behaviors": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

            generate_mock = AsyncMock()
            with patch("p2m.stages.design.generate_structured", generate_mock):
                result = await run_design(
                    policy_path=str(policy_path),
                    out_dir=str(root),
                    factors=[],
                    context="  Demo app  ",
                )

            payload = json.loads((root / "design.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {})
        self.assertEqual(payload["_context"], "Demo app")
        self.assertEqual(
            payload["behavior"][0],
            {"name": "Behavior A", "description": "Definition A"},
        )
        generate_mock.assert_not_awaited()

    async def test_run_design_merges_provided_and_generated_levels(self) -> None:
        from p2m.stages.design import run_design

        policy_payload = {
            "concept": {"name": "Risk"},
            "behaviors": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del prompt, options
            self.assertEqual(model, "azure/gpt-5.4")
            self.assertEqual(schema_name, "policy_design")
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
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

            with patch("p2m.stages.design.generate_structured", new=fake_generate_structured):
                result = await run_design(
                    policy_path=str(policy_path),
                    out_dir=str(root),
                    model="azure/gpt-5.4",
                    factors=[
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

            payload = json.loads((root / "design.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {"tone": 2, "audience": 2})
        self.assertEqual(payload["tone"][0]["name"], "Neutral")
        self.assertEqual(payload["audience"][1]["name"], "Expert")

    async def test_run_design_explicit_levels_without_description(self) -> None:
        """Factors with explicit levels should not require description."""
        from p2m.stages.design import run_design

        policy_payload = {
            "concept": {"name": "Risk"},
            "behaviors": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

            generate_mock = AsyncMock()
            with patch("p2m.stages.design.generate_structured", generate_mock):
                result = await run_design(
                    policy_path=str(policy_path),
                    out_dir=str(root),
                    factors=[
                        {
                            "name": "tone",
                            "levels": [
                                {"name": "Neutral", "definition": "Neutral wording."},
                                {"name": "Urgent", "definition": "Urgent wording."},
                            ],
                        },
                    ],
                )

            payload = json.loads((root / "design.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {"tone": 2})
        self.assertEqual(payload["tone"][0]["name"], "Neutral")
        generate_mock.assert_not_awaited()

    async def test_run_design_rejects_generated_factor_without_description(self) -> None:
        """Factors without levels must provide a description."""
        from p2m.stages.design import run_design

        policy_payload = {
            "concept": {"name": "Risk"},
            "behaviors": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

            with self.assertRaises(ValueError) as cm:
                await run_design(
                    policy_path=str(policy_path),
                    out_dir=str(root),
                    model="azure/gpt-5.4",
                    factors=[{"name": "tone"}],
                )

        self.assertIn("description is required", str(cm.exception))

    async def test_run_design_rejects_empty_levels(self) -> None:
        """Factors with levels: [] should be rejected."""
        from p2m.stages.design import run_design

        policy_payload = {
            "concept": {"name": "Risk"},
            "behaviors": [
                {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

            with self.assertRaises(ValueError) as cm:
                await run_design(
                    policy_path=str(policy_path),
                    out_dir=str(root),
                    factors=[{"name": "tone", "levels": []}],
                )

        self.assertIn("must not be empty", str(cm.exception))

    async def test_run_design_mixed_explicit_no_desc_and_generated(self) -> None:
        """Mixed: one factor with explicit levels (no description), one generated."""
        from p2m.stages.design import run_design

        policy_payload = {
            "concept": {"name": "Risk"},
            "behaviors": [
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
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

            with patch("p2m.stages.design.generate_structured", new=fake_generate_structured):
                result = await run_design(
                    policy_path=str(policy_path),
                    out_dir=str(root),
                    model="azure/gpt-5.4",
                    factors=[
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

            payload = json.loads((root / "design.json").read_text(encoding="utf-8"))

        self.assertEqual(result["factor_sizes"], {"tone": 2, "audience": 2})
        self.assertEqual(payload["tone"][0]["name"], "Neutral")
        self.assertEqual(payload["audience"][1]["name"], "Expert")
