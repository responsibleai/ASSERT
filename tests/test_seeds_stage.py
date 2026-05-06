import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from p2m.core.config_model import TargetConfig, ToolsConfig
from p2m.core.model_client import ModelResponse
from p2m.stages.design import normalize_design
from p2m.stages.seeds import run as run_stage, run_seeds


class SeedsStageTest(unittest.IsolatedAsyncioTestCase):
    async def test_stage_rejects_removed_validator_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "seeds validators are no longer supported"):
            await run_stage(
                {
                    "suite_root": Path("/tmp/demo-suite"),
                    "config_path": Path("/tmp/config.yaml"),
                    "artifacts_root": Path("/tmp/artifacts"),
                },
                {
                    "validator_model": "azure/gpt-5.4",
                    "prompt": {"model": {"name": "azure/gpt-5.4"}},
                },
            )

    async def test_stage_rejects_removed_modality_and_budget_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "seeds.scenario.modality is no longer supported"):
            await run_stage(
                {
                    "suite_root": Path("/tmp/demo-suite"),
                    "config_path": Path("/tmp/config.yaml"),
                    "artifacts_root": Path("/tmp/artifacts"),
                },
                {
                    "scenario": {"model": {"name": "azure/gpt-5.4"}, "modality": "agentic"},
                },
            )

        with self.assertRaisesRegex(ValueError, "seeds.prompt.budget was renamed to seeds.prompt.sample_size"):
            await run_stage(
                {
                    "suite_root": Path("/tmp/demo-suite"),
                    "config_path": Path("/tmp/config.yaml"),
                    "artifacts_root": Path("/tmp/artifacts"),
                },
                {
                    "prompt": {"model": {"name": "azure/gpt-5.4"}, "budget": 1},
                },
            )

    async def test_run_seeds_writes_runtime_rows(self) -> None:
        calls: list[str] = []
        prompt_texts: dict[str, str] = {}

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            calls.append(schema_name)
            prompt_texts[schema_name] = prompt
            if schema_name == "prompt_seeds":
                return ModelResponse(
                    parsed={"seeds": [{"description": "seed one"}]},
                    text="{}",
                    model="azure/gpt-5.4",
                )
            return ModelResponse(
                parsed={"seeds": [{"title": "Scenario A", "description": "Description A"}]},
                text="{}",
                model="azure/gpt-5.4",
            )

        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("p2m.stages.seeds.generate_structured", new=fake_generate_structured):
                result = await run_seeds(
                    taxonomy_path=str(taxonomy_path),
                    save_path=str(seeds_path),
                    context="A coding agent with filesystem and shell tools.",
                    prompt={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": 0.5,
                        "max_tokens": 1000,
                    },
                    scenario={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": 0.2,
                        "max_tokens": 1200,
                    },
                    target=TargetConfig(model="azure/gpt-5.4"),
                    tool_source="runtime",
                    design={"failure_mode": [{"name": "failure_mode-a", "description": "definition"}]},
                )

            rows = [json.loads(line) for line in seeds_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([row["kind"] for row in rows], ["prompt", "scenario"])
        self.assertEqual(rows[0]["seed"]["description"], "seed one")
        self.assertEqual(rows[1]["seed"]["description"], "Description A")
        self.assertEqual(result["seeds_path"], str(seeds_path))
        self.assertEqual(calls, ["prompt_seeds", "scenario_seeds"])
        self.assertIn("You write evaluation seeds: realistic standalone user messages", prompt_texts["prompt_seeds"])
        self.assertIn("A complete standalone user message.", prompt_texts["prompt_seeds"])
        self.assertIn("A coding agent with filesystem and shell tools.", prompt_texts["prompt_seeds"])
        self.assertIn(
            "You write scenario seeds for evaluation auditing.",
            prompt_texts["scenario_seeds"],
        )
        self.assertIn("one flowing second-person paragraph", prompt_texts["scenario_seeds"])
        self.assertIn("A coding agent with filesystem and shell tools.", prompt_texts["scenario_seeds"])
        self.assertNotIn("meta", rows[0])
        self.assertNotIn("meta", rows[1])

    async def test_run_seeds_omits_generated_system_prompts_when_target_prompt_is_fixed(self) -> None:
        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            del model, messages, schema_name, json_schema, options
            return ModelResponse(
                parsed={"seeds": [{"description": "seed one", "system_prompt": "per-seed prompt"}]},
                text="{}",
                model="azure/gpt-5.4",
            )

        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("p2m.stages.seeds.generate_structured", new=fake_generate_structured):
                await run_seeds(
                    taxonomy_path=str(taxonomy_path),
                    save_path=str(seeds_path),
                    context=None,
                    prompt={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": 0.5,
                        "max_tokens": 1000,
                    },
                    scenario=None,
                    target=TargetConfig(model="azure/gpt-5.4", system_prompt="fixed prompt"),
                    tool_source="runtime",
                    design={"failure_mode": [{"name": "failure_mode-a", "description": "definition"}]},
                )

            [row] = [json.loads(line) for line in seeds_path.read_text(encoding="utf-8").splitlines()]

        self.assertNotIn("system_prompt", row["seed"])

    async def test_run_seeds_per_seed_requires_simulator_target(self) -> None:
        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "seeds.tool_source=per_seed requires target.tools.simulator"):
                await run_seeds(
                    taxonomy_path=str(taxonomy_path),
                    save_path=str(seeds_path),
                    context=None,
                    prompt={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": 0.5,
                        "max_tokens": 1000,
                    },
                    scenario=None,
                    target=TargetConfig(model="azure/gpt-5.4"),
                    tool_source="per_seed",
                )

    async def test_run_seeds_per_seed_emits_tools_and_validates_shape(self) -> None:
        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            del model, messages, schema_name, json_schema, options
            return ModelResponse(
                parsed={
                    "seeds": [
                        {
                            "description": "seed one",
                            "tools": [
                                {
                                    "name": "lookup",
                                    "description": "Fetch account data.",
                                    "parameters": [
                                        {
                                            "name": "account_id",
                                            "type": "string",
                                            "description": "Customer account id.",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
                text="{}",
                model="azure/gpt-5.4",
            )

        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("p2m.stages.seeds.generate_structured", new=fake_generate_structured):
                await run_seeds(
                    taxonomy_path=str(taxonomy_path),
                    save_path=str(seeds_path),
                    context="Exploratory eval across toolsets.",
                    prompt={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": 0.5,
                        "max_tokens": 1000,
                    },
                    scenario=None,
                    target=TargetConfig(
                        model="azure/gpt-5.4",
                        tools=ToolsConfig(simulator="azure/gpt-5.4-mini"),
                    ),
                    tool_source="per_seed",
                    design={"failure_mode": [{"name": "failure_mode-a", "description": "definition"}]},
                )

            [row] = [json.loads(line) for line in seeds_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(row["seed"]["tools"][0]["name"], "lookup")

    async def test_run_seeds_injects_failure_mode_when_design_empty(self) -> None:
        prompts: list[str] = []

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, schema_name, json_schema, options
            prompts.append(prompt)
            return ModelResponse(
                parsed={"seeds": [{"description": "seed one"}]},
                text="{}",
                model="azure/gpt-5.4",
            )

        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["ex1"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("p2m.stages.seeds.generate_structured", new=fake_generate_structured):
                result = await run_seeds(
                    taxonomy_path=str(taxonomy_path),
                    save_path=str(seeds_path),
                    context="ctx",
                    prompt={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": None,
                        "max_tokens": 1000,
                    },
                    scenario=None,
                    target=TargetConfig(model="azure/gpt-5.4"),
                    design={},
                )

        self.assertEqual(result["saved_count"], 1)
        self.assertIn("ex1", prompts[0])

    async def test_run_seeds_persists_factor_assignments(self) -> None:
        prompts: list[str] = []

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, schema_name, json_schema, options
            prompts.append(prompt)
            return ModelResponse(
                parsed={"seeds": [{"description": "seed one"}]},
                text="{}",
                model="azure/gpt-5.4",
            )

        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }
        design = normalize_design(
            {
                "tone": [
                    {"name": "Neutral", "definition": "Neutral tone."},
                    {"name": "Urgent", "definition": "Urgent tone."},
                ]
            },
            taxonomy_payload,
            inject_failure_mode=True,
        )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "generated_seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("p2m.stages.seeds.generate_structured", new=fake_generate_structured):
                result = await run_seeds(
                    taxonomy_path=str(taxonomy_path),
                    save_path=str(seeds_path),
                    context="A helpful assistant.",
                    prompt={
                        "model": "azure/gpt-5.4",
                        "sample_size": 1,
                        "temperature": None,
                        "max_tokens": 1000,
                    },
                    scenario=None,
                    target=TargetConfig(model="azure/gpt-5.4"),
                    design=design,
                    seed=7,
                    concurrency=1,
                )

            [row] = [json.loads(line) for line in seeds_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(row["seed"]["description"], "seed one")
        self.assertEqual(result["saved_count"], 1)
        self.assertTrue(prompts)
        self.assertEqual(set(row["factors"]), {"failure_mode", "tone"})
        self.assertIn(row["factors"]["tone"], prompts[0])

    async def test_stage_passes_design_to_run_seeds(self) -> None:
        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }
        design_payload = {
            "tone": [
                {"name": "Neutral", "definition": "Neutral tone."},
                {"name": "Urgent", "definition": "Urgent tone."},
            ]
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            suite_root = tmp_path / "suite"
            suite_root.mkdir()
            config_path = tmp_path / "config.yaml"
            config_path.write_text("suite: demo\n", encoding="utf-8")
            (suite_root / "taxonomy.json").write_text(json.dumps(taxonomy_payload), encoding="utf-8")
            (suite_root / "design.json").write_text(json.dumps(design_payload), encoding="utf-8")

            run_seeds_mock = AsyncMock(return_value={"seeds_path": str(suite_root / "seeds.jsonl"), "saved_count": 1})
            with patch("p2m.stages.seeds.run_seeds", run_seeds_mock):
                result = await run_stage(
                    {
                        "suite_root": suite_root,
                        "config_path": config_path,
                        "artifacts_root": tmp_path / "artifacts",
                        "target": TargetConfig(model="azure/gpt-5.4"),
                        "context": "Runtime application context",
                    },
                    {
                        "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                    },
                )

        self.assertEqual(result["seeds_path"], str(suite_root / "seeds.jsonl"))
        self.assertEqual(run_seeds_mock.await_args.kwargs["context"], "Runtime application context")
        self.assertEqual(
            run_seeds_mock.await_args.kwargs["design"]["tone"][0]["name"],
            "Neutral",
        )

    async def test_stage_uses_failure_mode_only_design_when_design_file_missing(self) -> None:
        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            suite_root = tmp_path / "suite"
            suite_root.mkdir()
            config_path = tmp_path / "config.yaml"
            config_path.write_text("suite: demo\n", encoding="utf-8")
            (suite_root / "taxonomy.json").write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            run_seeds_mock = AsyncMock(return_value={"seeds_path": str(suite_root / "seeds.jsonl"), "saved_count": 1})
            with patch("p2m.stages.seeds.run_seeds", run_seeds_mock):
                result = await run_stage(
                    {
                        "suite_root": suite_root,
                        "config_path": config_path,
                        "artifacts_root": tmp_path / "artifacts",
                    },
                    {
                        "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                    },
                )

        self.assertEqual(result["seeds_path"], str(suite_root / "seeds.jsonl"))
        self.assertEqual(run_seeds_mock.await_args.kwargs["design"], {})

    async def test_stage_requires_design_file_when_factors_are_configured(self) -> None:
        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            suite_root = tmp_path / "suite"
            suite_root.mkdir()
            config_path = tmp_path / "config.yaml"
            config_path.write_text("suite: demo\n", encoding="utf-8")
            (suite_root / "taxonomy.json").write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "seed generation requires a design"):
                await run_stage(
                    {
                        "suite_root": suite_root,
                        "config_path": config_path,
                        "artifacts_root": tmp_path / "artifacts",
                        "factors": [{"name": "patient_type", "description": "The user type."}],
                    },
                    {
                        "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 1},
                    },
                )

    async def test_stage_rejects_legacy_method_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "seeds has unsupported field\\(s\\): method"):
            await run_stage(
                {
                    "suite_root": Path("/tmp/demo-suite"),
                    "config_path": Path("/tmp/config.yaml"),
                    "artifacts_root": Path("/tmp/artifacts"),
                },
                {
                    "method": "hard-assignment",
                    "prompt": {"model": {"name": "azure/gpt-5.4"}},
                },
            )

    async def test_run_seeds_per_seed_rejects_invalid_generated_tool_payloads(self) -> None:
        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            del model, messages, schema_name, json_schema, options
            return ModelResponse(
                parsed={"seeds": [{"description": "seed one", "tools": [{}]}]},
                text="{}",
                model="azure/gpt-5.4",
            )

        taxonomy_payload = {
            "spec": {"name": "Risk"},
            "failure_modes": [
                {"name": "failure_mode-a", "definition": "definition", "examples": ["example"], "permissible": False},
            ],
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            taxonomy_path = tmp_path / "taxonomy.json"
            seeds_path = tmp_path / "seeds.jsonl"
            taxonomy_path.write_text(json.dumps(taxonomy_payload), encoding="utf-8")

            with patch("p2m.stages.seeds.generate_structured", new=fake_generate_structured):
                with self.assertRaisesRegex(ValueError, "generated seed contains invalid tool definitions"):
                    await run_seeds(
                        taxonomy_path=str(taxonomy_path),
                        save_path=str(seeds_path),
                        context=None,
                        prompt={
                            "model": "azure/gpt-5.4",
                            "sample_size": 1,
                            "temperature": 0.5,
                            "max_tokens": 1000,
                        },
                        scenario=None,
                        target=TargetConfig(
                            model="azure/gpt-5.4",
                            tools=ToolsConfig(simulator="azure/gpt-5.4-mini"),
                        ),
                        tool_source="per_seed",
                        design={"failure_mode": [{"name": "failure_mode-a", "description": "definition"}]},
                    )


if __name__ == "__main__":
    unittest.main()
