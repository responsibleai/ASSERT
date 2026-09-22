# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.config import parse_model_config, parse_pipeline_config
from assert_ai.core import horse_client, judge as core_judge, model_client
from assert_ai.core.config_model import EvaluationConfig, InferenceConfig, JudgeConfig, TargetConfig, TesterConfig
from assert_ai.stages import inference, judge, stratification, systematize, test_set


def _model(label: str) -> dict:
    return {
        "name": f"horse/{label}",
        "max_tokens": 128,
        "temperature": 0.0,
        "horse": {
            "model_name": f"<{label}-virtual-model>",
            "renderer": "test-renderer",
        },
    }


def _completion(payload: dict | str) -> horse_client.HorseCompletion:
    return horse_client.HorseCompletion(
        text=json.dumps(payload) if isinstance(payload, dict) else payload,
        reasoning="",
        finish_reason="stop",
        raw={"messages": []},
    )


class HorsePipelineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.taxonomy = {
            "behavior": {"name": "greeting", "definition": "Respond to greetings politely."},
            "definition_of_terms": [],
            "behavior_categories": [
                {
                    "name": "polite-greeting",
                    "definition": "A polite greeting.",
                    "examples": ["Hello."],
                    "permissible": True,
                },
            ],
        }
        self.taxonomy_path = self.root / "taxonomy.json"
        self.taxonomy_path.write_text(json.dumps(self.taxonomy), encoding="utf-8")
        self.ctx = {
            "suite_root": self.root,
            "config_path": self.root / "config.yaml",
            "artifacts_root": self.root,
            "behavior_name": "greeting",
            "behavior": "Respond to greetings politely.",
            "context": "A greeting assistant.",
        }
        litellm = patch.object(model_client, "_get_litellm_module", side_effect=AssertionError("Unexpected LiteLLM call"))
        litellm.start()
        self.addCleanup(litellm.stop)
        bus = patch("assert_ai.core.bus_client.complete", side_effect=AssertionError("Unexpected BUS call"))
        bus.start()
        self.addCleanup(bus.stop)

    async def test_systematize_routes_research_and_conversion_to_horse(self) -> None:
        research = {
            "systematization": "The assistant should respond to greetings politely.",
            "summary_items": [{"description": "Polite greetings.", "example": "Hello."}],
        }
        with patch.object(
            horse_client, "complete", side_effect=[_completion(research), _completion(self.taxonomy)],
        ) as complete:
            result = await systematize.run(self.ctx, {"model": _model("generator"), "web_search": False})

        self.assertEqual(complete.await_count, 2)
        for call in complete.call_args_list:
            self.assertEqual(call.kwargs["config"].model_name, "<generator-virtual-model>")
            self.assertEqual(call.kwargs["max_tokens"], 128)
            self.assertEqual(call.kwargs["temperature"], 0.0)
        taxonomy = json.loads(Path(result["taxonomy_path"]).read_text(encoding="utf-8"))
        self.assertEqual(taxonomy["behavior"]["name"], "greeting")
        self.assertEqual(taxonomy["behavior_categories"][0]["name"], "polite-greeting")

    async def test_systematize_rejects_web_search_with_horse(self) -> None:
        with (
            patch.object(horse_client, "complete") as complete,
            self.assertRaisesRegex(ValueError, "Horse.*web_search"),
        ):
            await systematize.run(self.ctx, {"model": _model("generator")})
        complete.assert_not_called()

    async def test_legacy_systematize_helper_accepts_horse(self) -> None:
        config = parse_model_config(_model("generator"), field_name="model")
        with patch.object(horse_client, "complete", return_value=_completion(self.taxonomy)) as complete:
            result = await systematize.run_systematize(
                behavior="Respond to greetings politely.",
                model=config.name,
                horse_config=config.horse,
                save_dir=str(self.root),
            )
        self.assertIs(complete.call_args.kwargs["config"], config.horse)
        self.assertEqual(result["taxonomy"]["behavior"]["name"], "greeting")

    async def test_test_set_routes_prompt_scenario_and_variation_generation(self) -> None:
        async def complete(messages, *, config, max_tokens, temperature):
            if config.model_name == "<variations-virtual-model>":
                return _completion({
                    "tone": [
                        {"name": "casual", "definition": "Casual wording."},
                        {"name": "formal", "definition": "Formal wording."},
                    ],
                })
            self.assertIn(config.model_name, {"<prompt-virtual-model>", "<scenario-virtual-model>"})
            return _completion({"test_set": [{"description": "Say hello politely."}]})

        with (
            patch.object(horse_client, "complete", side_effect=complete) as generate,
            self.assertLogs("assert_ai.stages.stratification", level="WARNING") as logs,
        ):
            result = await test_set.run(
                self.ctx,
                {
                    "model": _model("variations"),
                    "stratify": {
                        "level_count": 2,
                        "dimensions": [{"name": "tone", "description": "How the greeting is phrased."}],
                    },
                    "prompt": {"model": _model("prompt"), "sample_size": 2},
                    "scenario": {"model": _model("scenario"), "sample_size": 2},
                },
            )

        self.assertTrue(any("without web search" in message for message in logs.output))
        self.assertEqual(
            {call.kwargs["config"].model_name for call in generate.call_args_list},
            {"<variations-virtual-model>", "<prompt-virtual-model>", "<scenario-virtual-model>"},
        )
        rows = [json.loads(line) for line in Path(result["test_set_path"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["type"] for row in rows}, {"prompt", "scenario"})
        self.assertTrue(all(row["dimensions"]["tone"] in {"casual", "formal"} for row in rows))

    async def test_stratification_entry_point_forwards_horse(self) -> None:
        with (
            patch.object(horse_client, "complete", return_value=_completion({
                "tone": [
                    {"name": "casual", "definition": "Casual wording."},
                    {"name": "formal", "definition": "Formal wording."},
                ],
            })) as complete,
            self.assertLogs("assert_ai.stages.stratification", level="WARNING"),
        ):
            result = await stratification.run(
                {**self.ctx, "dimensions": [{"name": "tone", "description": "How the greeting is phrased."}]},
                {"model": _model("variations"), "level_count": 2},
            )
        self.assertEqual(complete.call_args.kwargs["config"].model_name, "<variations-virtual-model>")
        self.assertEqual(result["_summary"]["factor_sizes"], {"tone": 2})

    async def test_inference_routes_prompt_target_and_scenario_tester(self) -> None:
        target = TargetConfig(model=parse_model_config(_model("target"), field_name="model"))
        evaluation = EvaluationConfig(
            tester=TesterConfig(model=parse_model_config(_model("tester"), field_name="model")),
            inference=InferenceConfig(max_turns=1),
        )
        test_case = {
            "test_case_id": "greeting-1",
            "behavior": "greeting",
            "seed": {"description": "Say hello."},
        }
        with patch.object(horse_client, "complete", return_value=_completion("Hello.")) as complete:
            prompt_transcript = await inference._run_prompt_test_case(
                test_case=test_case,
                target=target,
                inference=evaluation.inference,
                max_tokens=128,
                config_path=None,
            )
            scenario_transcript = await inference._run_scenario_test_case(
                test_case=test_case,
                target=target,
                evaluation=evaluation,
                max_tokens=128,
                config_path=None,
            )
        self.assertEqual(
            [call.kwargs["config"].model_name for call in complete.call_args_list],
            ["<target-virtual-model>", "<tester-virtual-model>", "<target-virtual-model>"],
        )
        self.assertEqual(prompt_transcript.stop_reason, "completed")
        self.assertNotIn(scenario_transcript.stop_reason, {"target_error", "tester_error"})
        self.assertTrue(scenario_transcript.events)

    async def test_judge_stage_routes_to_horse_and_invalidates_resume_on_model_change(self) -> None:
        target = TargetConfig(model=parse_model_config(_model("target"), field_name="model"))
        with patch.object(horse_client, "complete", return_value=_completion("Hello.")):
            transcript = await inference._run_prompt_test_case(
                test_case={"test_case_id": "greeting-1", "behavior": "greeting", "seed": {"description": "Hello."}},
                target=target, inference=InferenceConfig(), max_tokens=128, config_path=None,
            )
        inference_path = self.root / "inference_set.jsonl"
        transcript.save_jsonl(inference_path)
        verdict = {
            "dimensions": {"policy_violation": False, "overrefusal": False},
            "justification": "Polite greeting [1].",
            "dimension_justifications": {
                "policy_violation": "Polite greeting [1].",
                "overrefusal": "Answered the greeting [1].",
            },
            "node_judgments": [],
            "highlights": '1. <cite id="1" description="Greeting">Hello</cite>',
        }
        with patch.object(horse_client, "complete", return_value=_completion(verdict)) as complete:
            for label in ("judge", "judge", "changed-judge"):
                result = await judge.run_judge(
                    inference_set_path=str(inference_path),
                    taxonomy_path=str(self.taxonomy_path),
                    save_dir=str(self.root),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model=parse_model_config(
                            {**_model(label), "name": "horse/judge"}, field_name="model",
                        )),
                        inference=InferenceConfig(concurrency=1),
                    ),
                )
                self.assertEqual(result["count"], 1)
        self.assertEqual(
            [call.kwargs["config"].model_name for call in complete.call_args_list],
            ["<judge-virtual-model>", "<changed-judge-virtual-model>"],
        )
        [score] = [json.loads(line) for line in (self.root / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(score["judge_status"], "ok")

    async def test_shared_judge_helper_forwards_horse(self) -> None:
        config = parse_model_config(_model("judge"), field_name="model")
        with patch.object(horse_client, "complete", return_value=_completion({
            "dimensions": {"policy_violation": False},
            "justification": "Polite greeting.",
            "node_judgments": [],
        })) as complete:
            result = await core_judge.run_judge(
                judge_model=config.name,
                system_prompt="Judge greetings.",
                user_message="Hello.",
                score_keys=["policy_violation"],
                horse_config=config.horse,
            )
        self.assertIs(complete.call_args.kwargs["config"], config.horse)
        self.assertEqual(result["judge_status"], "ok")

    def test_default_model_routes_target_tester_and_judge(self) -> None:
        pipeline = parse_pipeline_config({
            "default_model": _model("default"),
            "pipeline": {
                "inference": {"target": {}, "tester": {}},
                "judge": {},
            },
        })
        for model in (pipeline.target.model, pipeline.evaluation.tester.model, pipeline.evaluation.judge.model):
            self.assertEqual(model.horse.model_name, "<default-virtual-model>")
            self.assertIsNone(model.bus)

    def test_inference_fingerprint_includes_target_and_tester_horse_settings(self) -> None:
        config = parse_model_config(_model("target"), field_name="model")
        target = TargetConfig(model=config)
        baseline = inference._inference_config_fingerprint(target, None, 128)
        for field, value in (("model_name", "<changed-virtual-model>"), ("renderer", "changed-renderer"), ("top_p", 0.5)):
            with self.subTest(field=field):
                changed = replace(config, horse=replace(config.horse, **{field: value}))
                self.assertNotEqual(
                    baseline, inference._inference_config_fingerprint(TargetConfig(model=changed), None, 128),
                )
                first = EvaluationConfig(tester=TesterConfig(model=config))
                second = EvaluationConfig(tester=TesterConfig(model=changed))
                self.assertNotEqual(
                    inference._inference_config_fingerprint(target, first, 128),
                    inference._inference_config_fingerprint(target, second, 128),
                )

    def test_judge_fingerprint_includes_horse_sampling_and_renderer(self) -> None:
        inference_path = self.root / "inference_set.jsonl"
        inference_path.write_text("{}\n", encoding="utf-8")
        horse = parse_model_config(_model("judge"), field_name="model").horse
        options = {
            "judge_model": "horse/judge",
            "judge_temperature": 0.0,
            "judge_max_tokens": 128,
            "judge_reasoning_effort": None,
            "judge_bus": None,
            "judge_n": 1,
            "judge_dimensions": [],
            "disabled_dimensions": [],
            "policy_raw": self.taxonomy,
            "system_prompt": "Judge greetings.",
            "inference_set_path": inference_path,
        }
        baseline = judge._judge_config_fingerprint(**options, judge_horse=asdict(horse))
        for field, value in (("renderer", "changed-renderer"), ("top_p", 0.5)):
            with self.subTest(field=field):
                self.assertNotEqual(
                    baseline,
                    judge._judge_config_fingerprint(**options, judge_horse=asdict(replace(horse, **{field: value}))),
                )
