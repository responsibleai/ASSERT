# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from assert_ai.core.artifact_cache import (
    activate_artifact_plan,
    finalize_artifact_plan,
    prepare_artifact_plan,
)
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
from assert_ai.core.model_client import estimate_token_count
from assert_ai.core.token_estimator import (
    _CaseProfile,
    _high_side_prompt_output,
    _project_prompt_case,
    _project_scenario_case,
    _representative_tool_value,
    _response_length_hint,
    _target_output,
    _transcript_xml,
    estimate_pipeline_tokens,
)
from assert_ai.core.transcript import (
    AddMessageEdit,
    Message as TranscriptMessage,
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)
from assert_ai.runner import estimate_pipeline_usage
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


def _tool_profile(kind: str) -> _CaseProfile:
    return _CaseProfile(
        kind=kind,
        test_case_id="tool-case",
        description="Calculate the sum of two numbers.",
        system_prompt="Use the calculator.",
        tools=({
            "name": "calculate",
            "parameters": [
                {"name": "left", "type": "integer"},
                {"name": "right", "type": "integer"},
            ],
        },),
    )


class TokenEstimatorTest(unittest.TestCase):
    def test_explicit_response_limits_have_conservative_headroom(self) -> None:
        for instruction, expected in [
            ("Reply in one sentence.", 96),
            ("Reply with the number and one short sentence.", 96),
            ("Define eventual consistency in no more than two sentences.", 160),
            ("Please respond using at most 50 words.", 132),
            ("Output exactly 100 tokens.", 157),
            ("Use exactly three sentences.", 224),
            ("Calculate the checksum and reply with one sentence.", 96),
            ("Explain caching in no more than two sentences.", 160),
        ]:
            with self.subTest(instruction=instruction):
                self.assertEqual(_response_length_hint(instruction), expected)
        self.assertEqual(
            _response_length_hint("Reply in one sentence.", "Reply in three sentences."), 224,
        )

    def test_length_hints_ignore_quoted_nested_negative_and_lower_bound_instructions(self) -> None:
        for instruction in [
            'Explain the phrase "reply in one sentence".',
            'Reply with "in one sentence" as part of a longer explanation.',
            "```text\nReply in one sentence.\n```",
            "```text\nReply in one sentence.",
            'Quoted task:\n"Introduction.\nReply in one sentence.\nEnd."',
            'Unclosed quote:\n"Introduction.\nReply in one sentence.',
            "Never reply in one sentence.",
            "Reply in not more than two sentences.",
            "Write at least 50 words.",
            "Write exactly two sentences per item.",
            "Write eight items, each with two sentences.",
            "Reply in one sentence, then write three paragraphs.",
            "Return exactly three bullets.",
            "Discuss 20 words that changed meaning.",
            "Describe a diagram with four words highlighted.",
            "Reply in one sentence or in two sentences.",
        ]:
            with self.subTest(instruction=instruction):
                self.assertIsNone(_response_length_hint(instruction))

    def test_prompt_length_hint_changes_projected_answer_and_judge_input(self) -> None:
        target = TargetConfig(model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=256))
        estimate, transcript, notes = _project_prompt_case(
            _CaseProfile("prompt", "short", "Reply in one sentence."),
            ctx={}, target=target, max_tokens=256,
        )
        self.assertEqual(estimate.output_tokens, 96)
        self.assertEqual(transcript.transcript_xml.count("response"), 96)
        self.assertTrue(any("response-length instructions" in note for note in notes))
        capped, _, _ = _project_prompt_case(
            _CaseProfile("prompt", "short", "Reply in one sentence."),
            ctx={}, target=TargetConfig(model=ModelConfig(name=target.model.name, max_tokens=64)),
            max_tokens=64,
        )
        self.assertEqual(capped.output_tokens, 56)

    def test_scenario_description_is_not_a_per_turn_answer_limit(self) -> None:
        target = TargetConfig(model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=256))
        evaluation = EvaluationConfig(
            tester=TesterConfig(model=target.model),
            inference=InferenceConfig(max_turns=2),
        )
        estimate, _, notes = _project_scenario_case(
            _CaseProfile("scenario", "multi", "Reply in one sentence."),
            ctx={}, target=target, evaluation=evaluation, max_tokens=256,
        )
        self.assertEqual(estimate.output_tokens, 2 * (224 + 55))
        self.assertFalse(any("response-length instructions" in note for note in notes))

    def test_target_output_leaves_headroom_without_lowering_large_budget_baseline(
        self,
    ) -> None:
        for limit, expected in [
            (1, 1), (64, 56), (256, 224), (512, 448),
            (900, 675), (1_000, 750), (4_000, 768), (None, 512),
        ]:
            with self.subTest(limit=limit):
                self.assertEqual(_high_side_prompt_output(limit), expected)
        self.assertEqual(_target_output(384, 256), 224)
        self.assertEqual(_target_output(384, 1_000), 384)

    def test_projected_transcript_matches_runtime_escaping_and_truncation(self) -> None:
        messages = [
            ("system", 'Keep "quotes" & <tags>.'),
            ("user", "x" * 10_001),
            ("assistant", ""),
            ("tool", "An ordinary tool result."),
        ]
        runtime = Transcript(
            metadata=TranscriptMetadata(
                kind="prompt", test_case_id="p1", behavior="", target="", tester_model="",
            ),
            events=[
                TranscriptEvent(
                    view=["target"], actor="target",
                    edit=AddMessageEdit(message=TranscriptMessage(role=role, content=content)),
                )
                for role, content in messages
            ],
        )
        expected, _ = runtime.format_transcript_xml("target", skip_system=False)
        projected = _transcript_xml(messages)
        self.assertEqual(projected, expected)
        self.assertIn('truncated="true"', projected)
        self.assertIn("10001 chars total", projected)
        self.assertIn("&lt;tags&gt;", projected)
        self.assertNotIn("x" * 10_001, projected)
        self.assertEqual(_transcript_xml([]), "<transcript>\n</transcript>")

    def test_representative_tool_arguments_preserve_schema_shape(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "mode": {"enum": ["fast", "slow"]},
                "items": {"type": "array", "items": {"type": "object", "properties": {}}},
                "unconstrained": {"type": "array", "items": True},
            },
        }
        self.assertEqual(_representative_tool_value(schema), {
            "city": "representative value", "count": 1, "ratio": 1,
            "enabled": True, "mode": "fast", "items": [{}],
            "unconstrained": ["representative value"],
        })

    def test_tool_projection_counts_call_json_instead_of_two_final_answers(self) -> None:
        for simulator in (None, "openai/gpt-4o-mini"):
            with self.subTest(simulator=simulator):
                target = TargetConfig(
                    model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=400),
                    tools=(
                        ToolsConfig(simulator=simulator)
                        if simulator
                        else ToolsConfig(module="example.tools")
                    ),
                )
                estimate, transcript, notes = _project_prompt_case(
                    _tool_profile("prompt"), ctx={}, target=target, max_tokens=400,
                )
                call_tokens = estimate_token_count(
                    target.model.name,
                    text=json.dumps({
                        "name": "calculate", "arguments": {"left": 1, "right": 1},
                    }),
                )
                self.assertEqual(estimate.calls, 3 if simulator else 2)
                self.assertEqual(
                    estimate.output_tokens,
                    350 + call_tokens + (90 if simulator else 0),
                )
                self.assertIn("[Tool call: calculate(", transcript.transcript_xml)
                self.assertIn("&quot;left&quot;: 1", transcript.transcript_xml)
                self.assertIn("<tool ", transcript.transcript_xml)
                self.assertTrue(any("one round trip" in note for note in notes))

    def test_scenario_projection_preserves_tool_history_and_fills_simulator_prompt(
        self,
    ) -> None:
        target = TargetConfig(
            model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=400),
            tools=ToolsConfig(simulator="openai/gpt-4o"),
        )
        evaluation = EvaluationConfig(
            tester=TesterConfig(model=ModelConfig(name="openai/gpt-4o-mini")),
            inference=InferenceConfig(max_turns=2),
        )
        requests = []

        def record_request(model, messages, **kwargs):
            requests.append((model, deepcopy(messages), kwargs))
            return 100

        with (
            patch("assert_ai.core.token_estimator._request_tokens", side_effect=record_request),
            patch("assert_ai.core.session.generate", side_effect=AssertionError("Provider called")),
        ):
            estimate, transcript, _ = _project_scenario_case(
                _tool_profile("scenario"), ctx={}, target=target,
                evaluation=evaluation, max_tokens=400,
            )

        self.assertEqual(estimate.calls, 8)
        self.assertEqual(estimate.input_tokens, 800)
        target_requests = [
            messages for _, messages, kwargs in requests if kwargs.get("tools")
        ]
        self.assertEqual(
            [sum(message.role == "tool" for message in messages) for messages in target_requests],
            [0, 1, 1, 2],
        )
        self.assertEqual(target_requests[1][2].tool_calls[0].arguments, {"left": 1, "right": 1})
        self.assertEqual(target_requests[1][3].tool_call_id, target_requests[1][2].tool_calls[0].id)
        self.assertNotEqual(target_requests[3][-1].tool_call_id, target_requests[1][-1].tool_call_id)
        simulator_prompts = [
            messages for model, messages, _ in requests if model == target.tools.simulator
        ]
        self.assertEqual(len(simulator_prompts), 2)
        for prompt in simulator_prompts:
            self.assertNotIn("{{", prompt)
            self.assertIn("Calculate the sum of two numbers.", prompt)
            self.assertIn("User: request", prompt)
            self.assertNotIn("Use the calculator.", prompt)
        self.assertIn("(none yet)", simulator_prompts[0])
        self.assertIn('- calculate({"left": 1, "right": 1}) -> result', simulator_prompts[1])
        self.assertIn("Target: response", simulator_prompts[1])
        self.assertEqual(transcript.transcript_xml.count("[Tool call: calculate("), 2)

    def test_config_estimate_is_read_only(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "eval.yaml"
            artifacts_root = root / "artifacts"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "suite": "preview-suite",
                        "run": "preview-run",
                        "artifacts_root": str(artifacts_root),
                        "behavior": {
                            "name": "answer_accuracy",
                            "description": "Answer accurately.",
                        },
                        "context": "A factual question answering assistant.",
                        "pipeline": {
                            "systematize": {
                                "behavior_category_count": 2,
                                "web_search": False,
                                "model": {
                                    "name": "openai/gpt-4o-mini",
                                    "max_tokens": 2_000,
                                },
                            },
                            "test_set": {
                                "prompt": {
                                    "sample_size": 2,
                                    "model": {
                                        "name": "openai/gpt-4o-mini",
                                        "max_tokens": 1_000,
                                    },
                                }
                            },
                            "inference": {
                                "target": {
                                    "model": {
                                        "name": "openai/gpt-4o-mini",
                                        "max_tokens": 512,
                                    }
                                }
                            },
                            "judge": {
                                "model": {
                                    "name": "openai/gpt-4o-mini",
                                    "max_tokens": 1_000,
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            estimate = estimate_pipeline_usage(config=str(config_path))

            self.assertGreater(estimate["total_tokens"], 0)
            self.assertEqual(
                set(estimate["stages"]),
                {"systematize", "test_set", "inference", "judge"},
            )
            self.assertFalse(artifacts_root.exists())

    def test_tool_limit_changes_upper_range_including_judge_but_not_point_estimate(self) -> None:
        for kind in ("prompt", "scenario"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                ctx = _base_context(root)
                suite_root = Path(ctx["suite_root"])
                profile = _tool_profile(kind)
                _write_taxonomy(suite_root / "taxonomy.json")
                _write_jsonl(suite_root / "test_set.jsonl", [{
                    "type": kind, "test_case_id": profile.test_case_id,
                    "seed": {
                        "description": profile.description,
                        "system_prompt": profile.system_prompt,
                        "tools": list(profile.tools),
                    },
                }])
                model = ModelConfig(name="openai/gpt-4o-mini", max_tokens=400)
                ctx["target"] = TargetConfig(
                    model=model, tools=ToolsConfig(simulator=model.name),
                )
                results = []
                for limit in (1, 10):
                    ctx["evaluation"] = EvaluationConfig(
                        tester=TesterConfig(model=model), judge=JudgeConfig(model=model),
                        inference=InferenceConfig(max_turns=2, max_tool_calls=limit),
                    )
                    inference = estimate_pipeline_tokens(ctx, [("inference", object(), {})])
                    combined = estimate_pipeline_tokens(
                        ctx, [("inference", object(), {}), ("judge", object(), {})],
                    )
                    results.append(combined)
                    self.assertGreater(
                        combined.tool_loop_total_tokens - combined.total_tokens,
                        inference.tool_loop_total_tokens - inference.total_tokens,
                    )
                    self.assertTrue(any(f"up to {limit} resolved" in n for n in combined.notes))
                self.assertEqual(results[0].total_tokens, results[1].total_tokens)
                self.assertGreater(results[1].upper_bound_tokens, results[0].upper_bound_tokens)

    def test_upper_tool_projection_counts_each_resolver_and_limit_fallback(self) -> None:
        target = TargetConfig(
            model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=400),
            tools=ToolsConfig(simulator="openai/gpt-4o"),
        )
        requests = []

        def record_request(model, messages, **kwargs):
            requests.append((model, deepcopy(messages), kwargs))
            return 100

        with patch("assert_ai.core.token_estimator._request_tokens", side_effect=record_request):
            estimate, transcript, _ = _project_prompt_case(
                _tool_profile("prompt"), ctx={}, target=target, max_tokens=400,
                tool_rounds=3, include_limit_fallback=True,
            )
        self.assertEqual(estimate.calls, 8)  # Initial + 3 follow-ups + forced final + 3 resolvers.
        self.assertEqual(sum(model == target.tools.simulator for model, _, _ in requests), 3)
        self.assertEqual(
            [sum(m.role == "tool" for m in messages) for model, messages, _ in requests
             if model == target.model.name],
            [0, 1, 2, 3, 4],
        )
        self.assertIsNone(requests[-1][2]["tools"])
        self.assertEqual(requests[-1][1][-1].text, "Tool call limit reached.")
        self.assertEqual(transcript.transcript_xml.count("[Tool call: calculate("), 4)

    def test_inference_only_estimate_reads_versioned_test_set_without_writes(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "eval.yaml"
            artifacts_root = root / "artifacts"
            results_dir = artifacts_root / "results"
            suite_root = results_dir / "preview-suite"
            suite_root.mkdir(parents=True)
            cache_ctx = {
                "config_path": config_path,
                "artifacts_root": artifacts_root,
                "suite_root": suite_root,
                "behavior_name": "answer_accuracy",
                "behavior": "Answer accurately.",
                "context": "A factual assistant.",
                "artifact_versions": {},
            }
            raw_test_set = {
                "prompt": {
                    "sample_size": 2,
                    "model": {"name": "openai/gpt-4o-mini"},
                }
            }
            plan = prepare_artifact_plan(
                ctx=cache_ctx,
                stage_name="test_set",
                raw_cfg=raw_test_set,
                forced=False,
            )
            activate_artifact_plan(cache_ctx, plan)
            _write_jsonl(
                plan.output_paths["test_set"],
                [
                    {
                        "type": "prompt",
                        "test_case_id": f"test_case_{index:06d}",
                        "seed": {
                            "description": f"Question {index}.",
                            "system_prompt": "Answer accurately.",
                        },
                    }
                    for index in (1, 2)
                ],
            )
            plan.output_paths["stratification"].write_text(
                "{}",
                encoding="utf-8",
            )
            finalize_artifact_plan(cache_ctx, plan)
            compatibility_path = suite_root / "test_set.jsonl"
            compatibility_path.unlink()
            latest_path = suite_root / "latest.json"
            latest_before = latest_path.read_bytes()

            config_path.write_text(
                yaml.safe_dump(
                    {
                        "suite": "preview-suite",
                        "run": "preview-run",
                        "artifacts_root": str(artifacts_root),
                        "context": "A factual assistant.",
                        "pipeline": {
                            "inference": {
                                "test_set_path": str(compatibility_path),
                                "target": {
                                    "model": {
                                        "name": "openai/gpt-4o-mini",
                                        "max_tokens": 512,
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            estimate = estimate_pipeline_usage(config=str(config_path))

            self.assertEqual(estimate["stages"]["inference"]["calls"], 2)
            self.assertEqual(latest_path.read_bytes(), latest_before)
            self.assertFalse(compatibility_path.exists())
            self.assertFalse((suite_root / "preview-run").exists())

    def test_judge_only_estimate_reads_versioned_taxonomy_without_writes(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "eval.yaml"
            artifacts_root = root / "artifacts"
            suite_root = artifacts_root / "results" / "preview-suite"
            run_root = suite_root / "preview-run"
            suite_root.mkdir(parents=True)
            cache_ctx = {
                "config_path": config_path,
                "artifacts_root": artifacts_root,
                "suite_root": suite_root,
                "behavior_name": "answer_accuracy",
                "behavior": "Answer accurately.",
                "context": "A factual assistant.",
                "artifact_versions": {},
            }
            raw_systematize = {
                "behavior_category_count": 2,
                "model": {"name": "openai/gpt-4o-mini"},
            }
            plan = prepare_artifact_plan(
                ctx=cache_ctx,
                stage_name="systematize",
                raw_cfg=raw_systematize,
                forced=False,
            )
            activate_artifact_plan(cache_ctx, plan)
            _write_taxonomy(plan.output_paths["taxonomy"])
            plan.output_paths["systematization"].write_text(
                "{}",
                encoding="utf-8",
            )
            finalize_artifact_plan(cache_ctx, plan)
            compatibility_path = suite_root / "taxonomy.json"
            compatibility_path.unlink()
            run_root.mkdir()
            inference_path = run_root / "inference_set.jsonl"
            _write_jsonl(
                inference_path,
                [
                    {
                        "type": "prompt",
                        "test_case_id": "test_case_000001",
                        "events": [],
                        "stop_reason": "completed",
                    }
                ],
            )
            latest_path = suite_root / "latest.json"
            latest_before = latest_path.read_bytes()

            config_path.write_text(
                yaml.safe_dump(
                    {
                        "suite": "preview-suite",
                        "run": "preview-run",
                        "artifacts_root": str(artifacts_root),
                        "behavior": {
                            "name": "answer_accuracy",
                            "description": "Answer accurately.",
                        },
                        "context": "A factual assistant.",
                        "pipeline": {
                            "judge": {
                                "taxonomy_path": str(compatibility_path),
                                "inference_set_path": str(inference_path),
                                "model": {
                                    "name": "openai/gpt-4o-mini",
                                    "max_tokens": 1_000,
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            estimate = estimate_pipeline_usage(config=str(config_path))

            self.assertEqual(estimate["stages"]["judge"]["calls"], 1)
            self.assertEqual(latest_path.read_bytes(), latest_before)
            self.assertFalse(compatibility_path.exists())

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
        self.assertEqual(estimate.stages["inference"].output_tokens, 1_500)
        self.assertEqual(estimate.stages["judge"].output_tokens, 1_024)
        self.assertGreater(estimate.input_tokens, 0)
        self.assertGreater(estimate.output_tokens, 0)
        self.assertEqual(estimate.uncertainty, 0.35)
        self.assertTrue(
            any("high-side output assumptions" in note for note in estimate.notes)
        )
        self.assertLess(
            estimate.lower_bound_tokens,
            estimate.total_tokens,
        )
        self.assertGreater(
            estimate.upper_bound_tokens,
            estimate.total_tokens,
        )

    def test_judge_output_grows_with_contract_and_respects_completion_limit(self) -> None:
        outputs = {}
        with TemporaryDirectory() as tmp_dir:
            ctx = _base_context(Path(tmp_dir))
            suite_root = Path(ctx["suite_root"])
            _write_jsonl(
                suite_root / "test_set.jsonl",
                [{"type": "prompt", "test_case_id": "p1", "seed": {"description": "Answer."}}],
            )
            ctx["target"] = TargetConfig(
                model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=256),
            )
            for categories, limit in [(1, 1_000), (20, 1_000), (20, 128)]:
                _write_taxonomy(suite_root / "taxonomy.json", categories)
                ctx["evaluation"] = EvaluationConfig(
                    judge=JudgeConfig(
                        model=ModelConfig(name="openai/gpt-4o-mini", max_tokens=limit),
                    ),
                )
                estimate = estimate_pipeline_tokens(
                    ctx, [("inference", object(), {}), ("judge", object(), {})],
                )
                outputs[(categories, limit)] = estimate.stages["judge"].output_tokens

        self.assertEqual(outputs[(1, 1_000)], 512)
        self.assertGreater(outputs[(20, 1_000)], outputs[(1, 1_000)])
        self.assertLessEqual(outputs[(20, 1_000)], 1_000)
        self.assertEqual(outputs[(20, 128)], 128)

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
