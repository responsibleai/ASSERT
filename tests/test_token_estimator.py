# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assert_ai.core.config_model import (
    DEFAULT_INFERENCE_MAX_TOKENS,
    EvaluationConfig,
    InferenceConfig,
    JudgeConfig,
    ModelConfig,
    TargetConfig,
    TesterConfig,
    ToolsConfig,
)
from assert_ai.core.judge import build_judge_contract
from assert_ai.core.io import (
    load_jsonl,
    normalize_test_case_rows,
    write_jsonl,
)
from assert_ai.core.token_estimator import estimate_pipeline_tokens
from assert_ai.stages import inference as inference_stage
from assert_ai.stages import judge as judge_stage


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _base_context(root: Path) -> dict[str, object]:
    config_path = root / "config.yaml"
    config_path.write_text("pipeline: {}\n", encoding="utf-8")
    suite_root = root / "results" / "suite"
    run_root = suite_root / "run"
    suite_root.mkdir(parents=True, exist_ok=True)
    return {
        "config_path": config_path,
        "artifacts_root": root,
        "suite_root": suite_root,
        "run_root": run_root,
        "behavior_name": "representative_behavior",
        "behavior": "The target must follow the configured behavior.",
        "context": "A representative application context.",
        "dimensions": [],
    }


def _write_taxonomy(path: Path, category_count: int = 2) -> None:
    path.write_text(
        json.dumps(
            {
                "behavior": {
                    "name": "representative_behavior",
                    "definition": "Required behavior.",
                },
                "definition_of_terms": [],
                "behavior_categories": [
                    {
                        "name": f"category_{index + 1}",
                        "definition": "Representative category.",
                        "examples": ["Representative example."],
                        "permissible": False,
                    }
                    for index in range(category_count)
                ],
            }
        ),
        encoding="utf-8",
    )


def _record_cached_compatibility_file(
    suite_root: Path,
    *,
    stage_name: str,
    output_key: str,
    compatibility_path: Path,
) -> None:
    version_dir = suite_root / "artifacts" / stage_name / "v0001"
    version_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(compatibility_path.read_bytes()).hexdigest()
    (version_dir / "artifact.json").write_text(
        json.dumps(
            {
                "files": {output_key: compatibility_path.name},
                "file_hashes": {output_key: digest},
            }
        ),
        encoding="utf-8",
    )


class TokenEstimatorTest(unittest.TestCase):
    def test_hosted_prompt_run_estimates_target_and_judge_calls(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            _write_taxonomy(suite_root / "taxonomy.json")
            _write_jsonl(
                suite_root / "test_set.jsonl",
                [
                    {
                        "type": "prompt",
                        "test_case_id": "p1",
                        "seed": {
                            "description": "Explain the first result.",
                            "system_prompt": "Answer accurately.",
                        },
                    },
                    {
                        "type": "prompt",
                        "test_case_id": "p2",
                        "seed": {
                            "description": "Explain the second result.",
                            "system_prompt": "Answer accurately.",
                        },
                    },
                ],
            )
            ctx["target"] = TargetConfig(
                model=ModelConfig(
                    name="openai/gpt-4o-mini",
                    max_tokens=1_000,
                )
            )
            ctx["evaluation"] = EvaluationConfig(
                judge=JudgeConfig(
                    model=ModelConfig(
                        name="openai/gpt-4o-mini",
                        max_tokens=1_000,
                    )
                )
            )

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    ("inference", object(), {}),
                    ("judge", object(), {}),
                ],
            )

        self.assertEqual(estimate.stages["inference"].calls, 2)
        self.assertEqual(estimate.stages["judge"].calls, 2)
        self.assertGreater(estimate.input_tokens, 0)
        self.assertGreater(estimate.output_tokens, 0)
        self.assertLess(
            estimate.lower_bound_tokens,
            estimate.total_tokens,
        )
        self.assertGreater(
            estimate.upper_bound_tokens,
            estimate.total_tokens,
        )

    def test_callable_scenario_excludes_unknown_target_usage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            _write_taxonomy(suite_root / "taxonomy.json")
            _write_jsonl(
                suite_root / "test_set.jsonl",
                [
                    {
                        "type": "scenario",
                        "test_case_id": "s1",
                        "seed": {
                            "description": "Apply pressure over several turns.",
                            "system_prompt": "Follow policy.",
                        },
                    }
                ],
            )
            model = ModelConfig(
                name="openai/gpt-4o-mini",
                max_tokens=1_000,
            )
            ctx["target"] = TargetConfig(callable="example.agent:chat")
            ctx["evaluation"] = EvaluationConfig(
                tester=TesterConfig(model=model),
                judge=JudgeConfig(model=model),
                inference=InferenceConfig(max_turns=3),
            )

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    ("inference", object(), {}),
                    ("judge", object(), {}),
                ],
            )

        self.assertEqual(estimate.stages["inference"].calls, 3)
        self.assertEqual(estimate.stages["judge"].calls, 1)
        self.assertTrue(
            any("callable target" in note for note in estimate.notes)
        )

    def test_first_run_estimates_generated_taxonomy_and_test_cases(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            ctx["dimensions"] = [
                {
                    "name": "pressure",
                    "description": "Pressure level.",
                    "levels": [
                        {"name": "low", "definition": "Low pressure."},
                        {"name": "high", "definition": "High pressure."},
                    ],
                }
            ]
            systematize_cfg = {
                "behavior_category_count": 4,
                "model": {
                    "name": "openai/gpt-4o-mini",
                    "max_tokens": 4_000,
                },
            }
            test_set_cfg = {
                "model": {
                    "name": "openai/gpt-4o-mini",
                    "max_tokens": 3_000,
                },
                "prompt": {"sample_size": 6},
                "scenario": {"sample_size": 3},
            }

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    ("systematize", object(), systematize_cfg),
                    ("test_set", object(), test_set_cfg),
                ],
            )

        self.assertEqual(estimate.stages["systematize"].calls, 2)
        self.assertGreaterEqual(estimate.stages["test_set"].calls, 2)
        self.assertGreater(estimate.total_tokens, 1_000)
        self.assertTrue(
            any("representative generated taxonomy" in note for note in estimate.notes)
        )

    def test_empty_test_set_kind_is_disabled_like_runtime(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            _write_taxonomy(Path(ctx["suite_root"]) / "taxonomy.json")

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    (
                        "test_set",
                        object(),
                        {
                            "model": {"name": "openai/gpt-4o-mini"},
                            "prompt": {},
                            "scenario": {"sample_size": 1},
                        },
                    )
                ],
            )

        self.assertEqual(estimate.stages["test_set"].calls, 1)

    def test_legacy_per_seed_matches_per_test_case_estimate(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            _write_taxonomy(Path(ctx["suite_root"]) / "taxonomy.json")
            model = ModelConfig(name="openai/gpt-4o-mini")
            ctx["target"] = TargetConfig(
                model=model,
                tools=ToolsConfig(simulator=model.name),
            )
            ctx["evaluation"] = EvaluationConfig()

            def estimate_for(tool_source: str):
                return estimate_pipeline_tokens(
                    ctx,
                    [
                        (
                            "test_set",
                            object(),
                            {
                                "tool_source": tool_source,
                                "model": {"name": model.name},
                                "prompt": {"sample_size": 1},
                            },
                        ),
                        ("inference", object(), {}),
                    ],
                )

            legacy = estimate_for("per_seed")
            canonical = estimate_for("per_test_case")

        self.assertEqual(legacy.to_dict(), canonical.to_dict())
        self.assertGreater(legacy.stages["inference"].calls, 1)

    def test_inference_resume_counts_only_pending_cases_unless_forced(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            test_set_path = suite_root / "test_set.jsonl"
            _write_jsonl(
                test_set_path,
                [
                    {
                        "type": "prompt",
                        "test_case_id": case_id,
                        "seed": {
                            "description": f"Prompt {case_id}.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                    for case_id in ("p1", "p2", "p3")
                ],
            )
            write_jsonl(
                test_set_path,
                normalize_test_case_rows(load_jsonl(test_set_path)),
            )
            model = ModelConfig(
                name="openai/gpt-4o-mini",
                max_tokens=1_000,
            )
            target = TargetConfig(model=model)
            evaluation = EvaluationConfig()
            ctx["target"] = target
            ctx["evaluation"] = evaluation
            _write_jsonl(
                run_root / "inference_set.jsonl",
                [
                    {
                        "type": "prompt",
                        "test_case_id": "test_case_000001",
                        "events": [],
                    }
                ],
            )
            fingerprint = inference_stage._inference_config_fingerprint(
                target,
                evaluation,
                DEFAULT_INFERENCE_MAX_TOKENS,
                test_set_path=test_set_path,
                config_path=Path(ctx["config_path"]),
            )
            (
                run_root / inference_stage._INFERENCE_CONFIG_HASH_FILE
            ).write_text(
                fingerprint,
                encoding="utf-8",
            )

            resumed = estimate_pipeline_tokens(
                ctx,
                [("inference", object(), {})],
            )
            forced = estimate_pipeline_tokens(
                ctx,
                [("inference", object(), {})],
                forced_stages={"inference"},
            )

        self.assertEqual(resumed.stages["inference"].calls, 2)
        self.assertEqual(forced.stages["inference"].calls, 3)

    def test_inference_resume_hashes_runtime_canonical_test_set(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            test_set_path = suite_root / "test_set.jsonl"
            _write_jsonl(
                test_set_path,
                [
                    {
                        "type": "prompt",
                        "test_case_id": "legacy-id",
                        "seed": {
                            "description": "Answer the prompt.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                ],
            )
            canonical_rows = normalize_test_case_rows(
                load_jsonl(test_set_path)
            )
            canonical_content = (
                os.linesep.join(
                    json.dumps(row, ensure_ascii=False)
                    for row in canonical_rows
                )
                + os.linesep
            ).encode("utf-8")
            target = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            evaluation = EvaluationConfig()
            ctx["target"] = target
            ctx["evaluation"] = evaluation
            _write_jsonl(
                run_root / "inference_set.jsonl",
                [
                    {
                        "type": "prompt",
                        "test_case_id": "test_case_000001",
                        "events": [],
                    }
                ],
            )
            fingerprint = inference_stage._inference_config_fingerprint(
                target,
                evaluation,
                DEFAULT_INFERENCE_MAX_TOKENS,
                test_set_path=test_set_path,
                config_path=Path(ctx["config_path"]),
                test_set_content=canonical_content,
            )
            (
                run_root / inference_stage._INFERENCE_CONFIG_HASH_FILE
            ).write_text(fingerprint, encoding="utf-8")

            estimate = estimate_pipeline_tokens(
                ctx,
                [("inference", object(), {})],
            )

        self.assertNotIn("inference", estimate.stages)

    def test_unrelated_test_set_output_does_not_invalidate_inference(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            _write_taxonomy(suite_root / "taxonomy.json")
            explicit_test_set = root / "fixed_test_set.jsonl"
            _write_jsonl(
                explicit_test_set,
                [
                    {
                        "type": "prompt",
                        "seed": {
                            "description": "Use the fixed input.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                ],
            )
            write_jsonl(
                explicit_test_set,
                normalize_test_case_rows(load_jsonl(explicit_test_set)),
            )
            target = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            evaluation = EvaluationConfig()
            ctx["target"] = target
            ctx["evaluation"] = evaluation
            _write_jsonl(
                run_root / "inference_set.jsonl",
                [
                    {
                        "type": "prompt",
                        "test_case_id": "test_case_000001",
                        "events": [],
                    }
                ],
            )
            fingerprint = inference_stage._inference_config_fingerprint(
                target,
                evaluation,
                DEFAULT_INFERENCE_MAX_TOKENS,
                test_set_path=explicit_test_set,
                config_path=Path(ctx["config_path"]),
            )
            (
                run_root / inference_stage._INFERENCE_CONFIG_HASH_FILE
            ).write_text(fingerprint, encoding="utf-8")

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    (
                        "test_set",
                        object(),
                        {
                            "model": {"name": "openai/gpt-4o-mini"},
                            "prompt": {"sample_size": 1},
                        },
                    ),
                    (
                        "inference",
                        object(),
                        {"test_set_path": str(explicit_test_set)},
                    ),
                ],
            )

        self.assertIn("test_set", estimate.stages)
        self.assertNotIn("inference", estimate.stages)

    def test_cache_compatibility_test_set_invalidates_inference(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            _write_taxonomy(suite_root / "taxonomy.json")
            compatibility_path = suite_root / "test_set.jsonl"
            _write_jsonl(
                compatibility_path,
                [
                    {
                        "type": "prompt",
                        "seed": {
                            "description": "Old cached prompt.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                ],
            )
            _record_cached_compatibility_file(
                suite_root,
                stage_name="test_set",
                output_key="test_set",
                compatibility_path=compatibility_path,
            )
            next_output = (
                suite_root
                / "artifacts"
                / "test_set"
                / "v0002"
                / "test_set.jsonl"
            )
            ctx["artifact_versions"] = {"test_set": {"version": "v0002"}}
            ctx["test_set_path"] = str(next_output)
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["evaluation"] = EvaluationConfig()

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    (
                        "test_set",
                        object(),
                        {
                            "save_path": str(next_output),
                            "model": {"name": "openai/gpt-4o-mini"},
                            "prompt": {"sample_size": 3},
                        },
                    ),
                    (
                        "inference",
                        object(),
                        {"test_set_path": str(compatibility_path)},
                    ),
                ],
            )

        self.assertEqual(estimate.stages["inference"].calls, 3)

    def test_local_test_set_edit_is_not_treated_as_cache_alias(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            _write_taxonomy(suite_root / "taxonomy.json")
            compatibility_path = suite_root / "test_set.jsonl"
            _write_jsonl(
                compatibility_path,
                [
                    {
                        "type": "prompt",
                        "seed": {
                            "description": "Locally edited prompt.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                ],
            )
            next_output = (
                suite_root
                / "artifacts"
                / "test_set"
                / "v0002"
                / "test_set.jsonl"
            )
            ctx["artifact_versions"] = {"test_set": {"version": "v0002"}}
            ctx["test_set_path"] = str(next_output)
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["evaluation"] = EvaluationConfig()

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    (
                        "test_set",
                        object(),
                        {
                            "save_path": str(next_output),
                            "model": {"name": "openai/gpt-4o-mini"},
                            "prompt": {"sample_size": 3},
                        },
                    ),
                    (
                        "inference",
                        object(),
                        {"test_set_path": str(compatibility_path)},
                    ),
                ],
            )

        self.assertEqual(estimate.stages["inference"].calls, 1)

    def test_partial_inference_merges_completed_and_projected_transcripts(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            _write_taxonomy(suite_root / "taxonomy.json")
            test_set_path = suite_root / "test_set.jsonl"
            _write_jsonl(
                test_set_path,
                [
                    {
                        "type": "prompt",
                        "seed": {
                            "description": f"Prompt {index}.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                    for index in (1, 2)
                ],
            )
            write_jsonl(
                test_set_path,
                normalize_test_case_rows(load_jsonl(test_set_path)),
            )
            target = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            evaluation = EvaluationConfig(
                judge=JudgeConfig(
                    model=ModelConfig(name="openai/gpt-4o-mini")
                )
            )
            ctx["target"] = target
            ctx["evaluation"] = evaluation
            _write_jsonl(
                run_root / "inference_set.jsonl",
                [
                    {
                        "type": "prompt",
                        "test_case_id": "test_case_000001",
                        "events": [],
                        "stop_reason": "target_error",
                    }
                ],
            )
            fingerprint = inference_stage._inference_config_fingerprint(
                target,
                evaluation,
                DEFAULT_INFERENCE_MAX_TOKENS,
                test_set_path=test_set_path,
                config_path=Path(ctx["config_path"]),
            )
            (
                run_root / inference_stage._INFERENCE_CONFIG_HASH_FILE
            ).write_text(fingerprint, encoding="utf-8")

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    ("inference", object(), {}),
                    ("judge", object(), {}),
                ],
            )

        self.assertEqual(estimate.stages["inference"].calls, 1)
        self.assertEqual(estimate.stages["judge"].calls, 1)

    def test_judge_resume_counts_only_pending_scores_unless_forced(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            taxonomy_path = suite_root / "taxonomy.json"
            _write_taxonomy(taxonomy_path)
            taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
            inference_path = run_root / "inference_set.jsonl"
            _write_jsonl(
                inference_path,
                [
                    {
                        "type": "prompt",
                        "test_case_id": case_id,
                        "events": [],
                        "stop_reason": "completed",
                    }
                    for case_id in ("p1", "p2")
                ],
            )
            _write_jsonl(
                run_root / "scores.jsonl",
                [{"type": "prompt", "test_case_id": "p1"}],
            )
            judge_cfg = JudgeConfig(
                model=ModelConfig(
                    name="openai/gpt-4o-mini",
                    max_tokens=1_000,
                )
            )
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["evaluation"] = EvaluationConfig(judge=judge_cfg)
            contract = build_judge_contract(
                template=judge_stage.JUDGE_SYSTEM_PROMPT,
                policy_raw=taxonomy,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                schema_name="transcript_judgment",
            )
            fingerprint = judge_stage._judge_config_fingerprint(
                judge_model=judge_cfg.model.name,
                judge_temperature=judge_cfg.model.temperature,
                judge_max_tokens=judge_cfg.model.max_tokens,
                judge_reasoning_effort=judge_cfg.model.reasoning_effort,
                judge_n=judge_cfg.n,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                policy_raw=taxonomy,
                system_prompt=contract["system_prompt"],
                inference_set_path=inference_path,
            )
            (run_root / judge_stage._JUDGE_CONFIG_HASH_FILE).write_text(
                fingerprint,
                encoding="utf-8",
            )

            resumed = estimate_pipeline_tokens(
                ctx,
                [("judge", object(), {})],
            )
            forced = estimate_pipeline_tokens(
                ctx,
                [("judge", object(), {})],
                forced_stages={"judge"},
            )

        self.assertEqual(resumed.stages["judge"].calls, 1)
        self.assertEqual(forced.stages["judge"].calls, 2)

    def test_unrelated_inference_output_does_not_invalidate_judge(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            taxonomy_path = suite_root / "taxonomy.json"
            _write_taxonomy(taxonomy_path)
            taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
            test_set_path = suite_root / "test_set.jsonl"
            _write_jsonl(
                test_set_path,
                [
                    {
                        "type": "prompt",
                        "seed": {
                            "description": "Run unrelated inference.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                ],
            )
            explicit_inference = root / "fixed_inference.jsonl"
            _write_jsonl(
                explicit_inference,
                [
                    {
                        "type": "prompt",
                        "test_case_id": "fixed-1",
                        "events": [],
                        "stop_reason": "completed",
                    }
                ],
            )
            _write_jsonl(
                run_root / "scores.jsonl",
                [{"type": "prompt", "test_case_id": "fixed-1"}],
            )
            judge_cfg = JudgeConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["evaluation"] = EvaluationConfig(judge=judge_cfg)
            contract = build_judge_contract(
                template=judge_stage.JUDGE_SYSTEM_PROMPT,
                policy_raw=taxonomy,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                schema_name="transcript_judgment",
            )
            fingerprint = judge_stage._judge_config_fingerprint(
                judge_model=judge_cfg.model.name,
                judge_temperature=judge_cfg.model.temperature,
                judge_max_tokens=judge_cfg.model.max_tokens,
                judge_reasoning_effort=judge_cfg.model.reasoning_effort,
                judge_n=judge_cfg.n,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                policy_raw=taxonomy,
                system_prompt=contract["system_prompt"],
                inference_set_path=explicit_inference,
            )
            (run_root / judge_stage._JUDGE_CONFIG_HASH_FILE).write_text(
                fingerprint,
                encoding="utf-8",
            )

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    ("inference", object(), {}),
                    (
                        "judge",
                        object(),
                        {"inference_set_path": str(explicit_inference)},
                    ),
                ],
            )

        self.assertEqual(estimate.stages["inference"].calls, 1)
        self.assertNotIn("judge", estimate.stages)

    def test_test_set_taxonomy_does_not_invalidate_judge_resume(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            test_set_taxonomy_path = root / "test_set_taxonomy.json"
            judge_taxonomy_path = root / "judge_taxonomy.json"
            _write_taxonomy(test_set_taxonomy_path, category_count=3)
            _write_taxonomy(judge_taxonomy_path, category_count=1)
            judge_taxonomy = json.loads(
                judge_taxonomy_path.read_text(encoding="utf-8")
            )
            inference_path = run_root / "inference_set.jsonl"
            _write_jsonl(
                inference_path,
                [
                    {
                        "type": "prompt",
                        "test_case_id": "p1",
                        "events": [],
                        "stop_reason": "completed",
                    }
                ],
            )
            _write_jsonl(
                run_root / "scores.jsonl",
                [{"type": "prompt", "test_case_id": "p1"}],
            )
            judge_cfg = JudgeConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["evaluation"] = EvaluationConfig(judge=judge_cfg)
            contract = build_judge_contract(
                template=judge_stage.JUDGE_SYSTEM_PROMPT,
                policy_raw=judge_taxonomy,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                schema_name="transcript_judgment",
            )
            fingerprint = judge_stage._judge_config_fingerprint(
                judge_model=judge_cfg.model.name,
                judge_temperature=judge_cfg.model.temperature,
                judge_max_tokens=judge_cfg.model.max_tokens,
                judge_reasoning_effort=judge_cfg.model.reasoning_effort,
                judge_n=judge_cfg.n,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                policy_raw=judge_taxonomy,
                system_prompt=contract["system_prompt"],
                inference_set_path=inference_path,
            )
            (run_root / judge_stage._JUDGE_CONFIG_HASH_FILE).write_text(
                fingerprint,
                encoding="utf-8",
            )

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    (
                        "test_set",
                        object(),
                        {
                            "taxonomy_path": str(test_set_taxonomy_path),
                            "save_path": str(root / "generated.jsonl"),
                            "model": {"name": "openai/gpt-4o-mini"},
                            "prompt": {"sample_size": 1},
                        },
                    ),
                    (
                        "judge",
                        object(),
                        {"taxonomy_path": str(judge_taxonomy_path)},
                    ),
                ],
            )

        self.assertIn("test_set", estimate.stages)
        self.assertNotIn("judge", estimate.stages)

    def test_cache_compatibility_taxonomy_invalidates_judge(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ctx = _base_context(root)
            suite_root = Path(ctx["suite_root"])
            run_root = Path(ctx["run_root"])
            run_root.mkdir(parents=True)
            compatibility_path = suite_root / "taxonomy.json"
            _write_taxonomy(compatibility_path, category_count=1)
            old_taxonomy = json.loads(
                compatibility_path.read_text(encoding="utf-8")
            )
            _record_cached_compatibility_file(
                suite_root,
                stage_name="systematize",
                output_key="taxonomy",
                compatibility_path=compatibility_path,
            )
            next_output_dir = (
                suite_root / "artifacts" / "systematize" / "v0002"
            )
            ctx["artifact_versions"] = {
                "systematize": {"version": "v0002"}
            }
            ctx["systematize_artifact_dir"] = str(next_output_dir)
            ctx["taxonomy_path"] = str(next_output_dir / "taxonomy.json")
            inference_path = run_root / "inference_set.jsonl"
            _write_jsonl(
                inference_path,
                [
                    {
                        "type": "prompt",
                        "test_case_id": "p1",
                        "events": [],
                        "stop_reason": "completed",
                    }
                ],
            )
            _write_jsonl(
                run_root / "scores.jsonl",
                [{"type": "prompt", "test_case_id": "p1"}],
            )
            judge_cfg = JudgeConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini")
            )
            ctx["evaluation"] = EvaluationConfig(judge=judge_cfg)
            contract = build_judge_contract(
                template=judge_stage.JUDGE_SYSTEM_PROMPT,
                policy_raw=old_taxonomy,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                schema_name="transcript_judgment",
            )
            fingerprint = judge_stage._judge_config_fingerprint(
                judge_model=judge_cfg.model.name,
                judge_temperature=judge_cfg.model.temperature,
                judge_max_tokens=judge_cfg.model.max_tokens,
                judge_reasoning_effort=judge_cfg.model.reasoning_effort,
                judge_n=judge_cfg.n,
                judge_dimensions=judge_cfg.dimensions,
                disabled_dimensions=judge_cfg.disabled_dimensions,
                policy_raw=old_taxonomy,
                system_prompt=contract["system_prompt"],
                inference_set_path=inference_path,
            )
            (run_root / judge_stage._JUDGE_CONFIG_HASH_FILE).write_text(
                fingerprint,
                encoding="utf-8",
            )

            estimate = estimate_pipeline_tokens(
                ctx,
                [
                    (
                        "systematize",
                        object(),
                        {
                            "save_dir": str(next_output_dir),
                            "behavior_category_count": 3,
                            "model": {
                                "name": "openai/gpt-4o-mini",
                                "max_tokens": 4_000,
                            },
                        },
                    ),
                    (
                        "judge",
                        object(),
                        {"taxonomy_path": str(compatibility_path)},
                    ),
                ],
            )

        self.assertEqual(estimate.stages["judge"].calls, 1)


if __name__ == "__main__":
    unittest.main()
