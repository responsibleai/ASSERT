# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.config_model import (
    EndpointConfig,
    EvaluationConfig,
    InferenceConfig,
    JudgeConfig,
    TargetConfig,
    TesterConfig,
    ToolsConfig,
)
from assert_ai.core.io import load_test_cases
from assert_ai.core.model_client import LLMInputError, LLMProviderError, Message, ModelResponse
from assert_ai.core.session import TurnResult
from assert_ai.stages.inference import (
    _inference_config_fingerprint,
    _prepare_test_cases,
    _run_prompt_test_case,
    run_inference,
)
from assert_ai.viewer_read_model import ViewerReadModelBuildError


class InferenceStageTest(unittest.IsolatedAsyncioTestCase):
    def test_inference_config_fingerprint_changes_with_seeds_content(self) -> None:
        """Fingerprint must include seed content so regenerating test_set invalidates cached transcripts.

        Without this, the resume path keys on test_case_id alone, and test case ids
        are deterministic enough that fresh test_set collide with cached
        transcripts. Regression test for the Apr 28 cache-invalidation fix.
        """
        target = TargetConfig(model="azure/gpt-5.4")
        evaluation = EvaluationConfig(
            inference=InferenceConfig(max_turns=4, concurrency=1),
            judge=JudgeConfig(model="azure/gpt-5.4"),
            tester=TesterConfig(model="azure/gpt-5.4"),
        )
        with TemporaryDirectory() as tmp_dir:
            test_set_a = Path(tmp_dir) / "test_set_a.jsonl"
            test_set_b = Path(tmp_dir) / "test_set_b.jsonl"
            test_set_a.write_text(
                '{"type":"prompt","test_case_id":"prompt-x-001","content":"first"}\n',
                encoding="utf-8",
            )
            test_set_b.write_text(
                '{"type":"prompt","test_case_id":"prompt-x-001","content":"second"}\n',
                encoding="utf-8",
            )

            hash_no_seeds = _inference_config_fingerprint(target, evaluation, 1024)
            hash_a = _inference_config_fingerprint(target, evaluation, 1024, test_set_path=test_set_a)
            hash_b = _inference_config_fingerprint(target, evaluation, 1024, test_set_path=test_set_b)

        # Including test_set_path must materially change the fingerprint
        # versus the legacy no-test_set form, and two different seed files
        # with the same test_case_id but different content must hash apart.
        self.assertNotEqual(hash_a, hash_no_seeds)
        self.assertNotEqual(hash_a, hash_b)

    def test_inference_config_fingerprint_uses_endpoint_url_for_mapping_config(self) -> None:
        target = TargetConfig(
            endpoint=EndpointConfig(
                url="http://localhost:8000/v1/chat/completions",
                protocol="openai_chat",
                model="custom-agent",
            )
        )
        evaluation = EvaluationConfig(
            inference=InferenceConfig(max_turns=4, concurrency=1),
            judge=JudgeConfig(model="azure/gpt-5.4"),
            tester=TesterConfig(model="azure/gpt-5.4"),
        )

        self.assertEqual(
            _inference_config_fingerprint(target, evaluation, 1024),
            _inference_config_fingerprint(target, evaluation, 1024),
        )
        same_url_other_model = TargetConfig(
            endpoint=EndpointConfig(
                url="http://localhost:8000/v1/chat/completions",
                protocol="openai_chat",
                model="other-agent",
            )
        )
        other_target = TargetConfig(endpoint="http://localhost:9000/chat")
        self.assertNotEqual(
            _inference_config_fingerprint(target, evaluation, 1024),
            _inference_config_fingerprint(same_url_other_model, evaluation, 1024),
        )
        self.assertNotEqual(
            _inference_config_fingerprint(target, evaluation, 1024),
            _inference_config_fingerprint(other_target, evaluation, 1024),
        )

    def test_prepare_test_cases_rejects_non_empty_seed_prompt_when_target_prompt_is_fixed(self) -> None:
        rows = [
            {
                "type": "prompt",
                "seed": {
                    "description": "seed prompt",
                    "system_prompt": "per-seed prompt",
                },
            }
        ]
        with self.assertRaisesRegex(
            ValueError,
            "target.system_prompt cannot be combined with non-empty test case system_prompt",
        ):
            _prepare_test_cases(
                rows,
                tool_source="runtime",
                fixed_system_prompt="fixed prompt",
            )

    def test_prepare_test_cases_treats_empty_seed_prompt_as_absent(self) -> None:
        rows = [
            {
                "type": "prompt",
                "seed": {
                    "description": "seed prompt",
                    "system_prompt": "   ",
                },
            }
        ]
        test_set = _prepare_test_cases(
            rows,
            tool_source="runtime",
            fixed_system_prompt=None,
        )

        self.assertNotIn("system_prompt", test_set[0]["seed"])

    def test_prepare_test_cases_validates_per_test_case_tools(self) -> None:
        rows = [
            {
                "type": "prompt",
                "seed": {
                    "description": "seed prompt",
                    "tools": [
                        {
                            "name": "lookup",
                            "description": "Fetch account data.",
                            "parameters": [
                                {"name": "account_id", "type": "string", "description": "Customer account id."}
                            ],
                        }
                    ],
                },
            }
        ]
        test_set = _prepare_test_cases(
            rows,
            tool_source="per_test_case",
            fixed_system_prompt=None,
        )

        self.assertEqual(test_set[0]["seed"]["tools"][0]["name"], "lookup")

    def test_prepare_test_cases_rejects_seed_tools_for_runtime_tool_source(self) -> None:
        rows = [
            {
                "type": "prompt",
                "seed": {
                    "description": "seed prompt",
                    "tools": [
                        {
                            "name": "lookup",
                            "description": "Fetch account data.",
                            "parameters": [
                                {"name": "account_id", "type": "string", "description": "Customer account id."}
                            ],
                        }
                    ],
                },
            }
        ]
        with self.assertRaisesRegex(ValueError, "test case tools are only allowed when tool_source=per_test_case"):
            _prepare_test_cases(
                rows,
                tool_source="runtime",
                fixed_system_prompt=None,
            )

    async def test_run_inference_uses_fixed_target_prompt_exactly(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {"description": "seed prompt"},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }
        captured_messages: list[Message] = []

        class FakeSession:
            runtime_mode = "tool_module"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                captured_messages[:] = list(initial_messages)
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {"role": "assistant", "content": "model response"},
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4", system_prompt="You are a coding agent."),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

        self.assertEqual(captured_messages[0].role, "system")
        self.assertEqual(captured_messages[0].content, "You are a coding agent.")
        self.assertEqual(captured_messages[1].content, "seed prompt")

    async def test_run_inference_uses_per_test_case_prompt_exactly(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {
                "description": "seed prompt",
                "system_prompt": "Per-seed prompt",
            },
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }
        captured_messages: list[Message] = []

        class FakeSession:
            runtime_mode = "chat"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                captured_messages[:] = list(initial_messages)
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {"role": "assistant", "content": "model response"},
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

        self.assertEqual(captured_messages[0].role, "system")
        self.assertEqual(captured_messages[0].content, "Per-seed prompt")

    async def test_run_inference_can_leave_versioned_seed_file_unchanged(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "original-seed-id",
            "seed": {"description": "seed prompt"},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }

        class FakeSession:
            runtime_mode = "chat"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {"role": "assistant", "content": "model response"},
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            original_test_case_text = json.dumps(test_case_row) + "\n"
            test_set_path.write_text(original_test_case_text, encoding="utf-8")

            with patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                    rewrite_test_set_path=False,
                )

            self.assertEqual(test_set_path.read_text(encoding="utf-8"), original_test_case_text)

    async def test_run_inference_never_mutates_versioned_seed_artifact(self) -> None:
        """Even when callers pass rewrite_test_set_path=True, files under
        artifacts/test_set/v#### must be left intact so the cached file_hashes
        stay valid."""

        test_case_row = {
            "type": "prompt",
            "test_case_id": "original-seed-id",
            "seed": {"description": "seed prompt"},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }

        class FakeSession:
            runtime_mode = "chat"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {"role": "assistant", "content": "model response"},
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            versioned_dir = tmp_path / "suite" / "artifacts" / "test_set" / "v0001"
            versioned_dir.mkdir(parents=True, exist_ok=True)
            test_set_path = versioned_dir / "test_set.jsonl"
            out_dir = tmp_path / "run"
            original_test_case_text = json.dumps(test_case_row) + "\n"
            test_set_path.write_text(original_test_case_text, encoding="utf-8")

            with patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                    rewrite_test_set_path=True,
                )

            self.assertEqual(test_set_path.read_text(encoding="utf-8"), original_test_case_text)

    async def test_run_inference_fails_when_viewer_artifact_build_fails(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {"description": "seed prompt"},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }

        class FakeSession:
            runtime_mode = "chat"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {"role": "assistant", "content": "model response"},
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with (
                patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()),
                patch(
                    "assert_ai.stages.inference.build_run_viewer_artifacts",
                    side_effect=ViewerReadModelBuildError("viewer build failed"),
                ),
            ):
                with self.assertRaisesRegex(ViewerReadModelBuildError, "viewer build failed"):
                    await run_inference(
                        test_set_path=str(test_set_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                        save_dir=str(out_dir),
                        run_id="run-inference",
                    )

    async def test_run_inference_persists_owned_llm_calls_and_links_message_ids(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {"description": "seed prompt"},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }

        class FakeSession:
            runtime_mode = "chat"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {
                            "role": "assistant",
                            "content": "model response",
                            "llm_call_index": 0,
                            "raw": {"response": {"content": "model response"}},
                        },
                    ],
                    llm_calls=[
                        {
                            "source": "target",
                            "api_mode": "chat_completion",
                            "request": {"model": "azure/gpt-5.4", "messages": [{"role": "user", "content": "seed prompt"}]},
                            "response": {"id": "resp_1", "choices": []},
                            "derived": {"content": "model response", "stop_reason": "stop"},
                        }
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            [inference_row] = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(inference_row["llm_calls"][0]["source"], "target")
        self.assertEqual(inference_row["llm_calls"][0]["request"]["model"], "azure/gpt-5.4")
        self.assertEqual(inference_row["llm_calls"][0]["response"]["id"], "resp_1")
        self.assertEqual(inference_row["llm_calls"][0]["message_ids"], ["event:1"])

    async def test_run_inference_sets_runtime_close_error_stop_reason(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {"description": "seed prompt"},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }

        class FakeSession:
            runtime_mode = "tool_module"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                raise RuntimeError("close failed")

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="model response",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="model response")],
                    interaction_messages=[
                        {"role": "user", "content": "seed prompt"},
                        {"role": "assistant", "content": "model response"},
                    ],
                    raw={"response": {"content": "model response"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(inference_rows[0]["stop_reason"], "runtime_close_error")

    async def test_run_inference_external_transcript_writes_minimal_rows(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {"description": "Please help", "system_prompt": "You are a health assistant."},
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
        }

        class FakeExternalSession:
            runtime_mode = "external"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="session reply",
                    state_messages=list(initial_messages),
                    interaction_messages=[
                        {"role": "user", "content": "Please help"},
                        {"role": "assistant", "content": "session reply"},
                    ],
                    raw={"response": {"content": "session reply"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference.ExternalSession", return_value=FakeExternalSession()):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(connector="examples.agents.demo"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertNotIn("metadata", inference_rows[0])
        self.assertNotIn("run_id", inference_rows[0])
        self.assertEqual(inference_rows[0]["events"][0]["edit"]["message"]["content"], "You are a health assistant.")

    async def test_run_inference_scenario_keeps_setup_hidden_for_external_target(self) -> None:
        test_case_row = {
            "type": "scenario",
            "test_case_id": "test-case-1",
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
            "seed": {
                "title": "Title",
                "description": "Description",
                "system_prompt": "You are a careful health assistant.",
            },
        }

        async def fake_generate(model, messages, options):
            del model, messages, options
            return ModelResponse(text="Hello target", model="azure/gpt-5.4")

        class FakeExternalSession:
            runtime_mode = "external"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="Target reply",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="Target reply")],
                    interaction_messages=[
                        {"role": "user", "content": "Hello target"},
                        {"role": "assistant", "content": "Target reply"},
                    ],
                    raw={"response": {"content": "Target reply"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with (
                patch("assert_ai.stages.inference.generate", new=fake_generate),
                patch("assert_ai.stages.inference.ExternalSession", return_value=FakeExternalSession()),
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(connector="examples.agents.demo"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(max_turns=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        target_events = [
            event
            for event in inference_rows[0]["events"]
            if "target" in event["view"] and event["edit"]["type"] == "add_message"
        ]
        self.assertEqual(target_events[0]["edit"]["message"]["role"], "user")
        self.assertEqual(target_events[0]["edit"]["message"]["content"], "Hello target")
        self.assertEqual(inference_rows[0]["events"][0]["edit"]["type"], "set_system_message")
        self.assertEqual(
            inference_rows[0]["events"][0]["edit"]["message"]["content"],
            "You are a careful health assistant.",
        )

    async def test_run_inference_scenario_forwards_setup_like_text_unchanged(self) -> None:
        test_case_row = {
            "type": "scenario",
            "test_case_id": "test-case-1",
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
            "seed": {
                "title": "Title",
                "description": "Description",
                "system_prompt": "You are a careful health assistant for Mara.",
            },
        }
        call_counter = {"n": 0}

        async def fake_generate(model, messages, options):
            del model, messages, options
            responses = [
                ModelResponse(text="Be a careful health assistant for Mara.", model="azure/gpt-5.4"),
                ModelResponse(text="hey, can you help me figure out this headache?", model="azure/gpt-5.4"),
            ]
            response = responses[min(call_counter["n"], len(responses) - 1)]
            call_counter["n"] += 1
            return response

        class FakeExternalSession:
            runtime_mode = "external"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                return TurnResult(
                    text="Target reply",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="Target reply")],
                    interaction_messages=[
                        {"role": "user", "content": "hey, can you help me figure out this headache?"},
                        {"role": "assistant", "content": "Target reply"},
                    ],
                    raw={"response": {"content": "Target reply"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with (
                patch("assert_ai.stages.inference.generate", new=fake_generate),
                patch("assert_ai.stages.inference.ExternalSession", return_value=FakeExternalSession()),
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(connector="examples.agents.demo"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(max_turns=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        target_events = [
            event
            for event in inference_rows[0]["events"]
            if "target" in event["view"] and event["edit"]["type"] == "add_message"
        ]
        # With effective_system_message, the tester's first attempt ("Be a careful
        # health assistant for Mara.") is detected as hidden setup and retried.
        # The second tester response is forwarded to the external target.
        self.assertEqual(target_events[0]["edit"]["message"]["content"], "hey, can you help me figure out this headache?")

    async def test_run_inference_scenario_keeps_setup_hidden_for_hosted_target(self) -> None:
        test_case_row = {
            "type": "scenario",
            "test_case_id": "test-case-1",
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
            "seed": {
                "title": "Title",
                "description": "Description",
                "system_prompt": "You are a careful health assistant.",
            },
        }
        captured_inputs: list[Message] = []

        async def fake_generate(model, messages, options):
            del model, messages, options
            return ModelResponse(text="Hello target", model="azure/gpt-5.4")

        class FakeHostedSession:
            runtime_mode = "chat"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, initial_messages):
                captured_inputs[:] = list(initial_messages)
                return TurnResult(
                    text="Target reply",
                    state_messages=list(initial_messages) + [Message(role="assistant", content="Target reply")],
                    interaction_messages=[
                        {"role": "user", "content": "Hello target"},
                        {"role": "assistant", "content": "Target reply"},
                    ],
                    raw={"response": {"content": "Target reply"}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with (
                patch("assert_ai.stages.inference.generate", new=fake_generate),
                patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeHostedSession()),
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(max_turns=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

        self.assertEqual(captured_inputs[0].role, "system")
        self.assertEqual(captured_inputs[0].content, "You are a careful health assistant.")
        user_messages = [message for message in captured_inputs if message.role == "user"]
        self.assertEqual([message.content for message in user_messages], ["Hello target"])

    async def test_run_inference_scenario_keeps_setup_hidden_for_other_hosted_runtime_modes(self) -> None:
        test_case_row = {
            "type": "scenario",
            "test_case_id": "test-case-1",
            "behavior": "Risk",
            "dimensions": {"behavior": "behavior-a"},
            "seed": {
                "title": "Title",
                "description": "Description",
                "system_prompt": "You are a careful health assistant.",
            },
        }

        async def fake_generate(model, messages, options):
            del model, messages, options
            return ModelResponse(text="Hello target", model="azure/gpt-5.4")

        for runtime_mode in ("tool_module", "simulated"):
            captured_inputs: list[Message] = []

            class FakeHostedSession:
                async def open(self) -> None:
                    return None

                async def close(self) -> None:
                    return None

                async def run_turn(self, initial_messages):
                    captured_inputs[:] = list(initial_messages)
                    return TurnResult(
                        text="Target reply",
                        state_messages=list(initial_messages) + [Message(role="assistant", content="Target reply")],
                        interaction_messages=[
                            {"role": "user", "content": "Hello target"},
                            {"role": "assistant", "content": "Target reply"},
                        ],
                        raw={"response": {"content": "Target reply"}},
                    )

            FakeHostedSession.runtime_mode = runtime_mode

            with TemporaryDirectory() as tmp_dir, self.subTest(runtime_mode=runtime_mode):
                tmp_path = Path(tmp_dir)
                test_set_path = tmp_path / "test_set.jsonl"
                out_dir = tmp_path / "run"
                test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

                with (
                    patch("assert_ai.stages.inference.generate", new=fake_generate),
                    patch("assert_ai.stages.inference._build_hosted_session", return_value=FakeHostedSession()),
                ):
                    await run_inference(
                        test_set_path=str(test_set_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            tester=TesterConfig(model="azure/gpt-5.4"),
                            inference=InferenceConfig(max_turns=1),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-inference",
                    )

            self.assertEqual(captured_inputs[0].role, "system")
            self.assertEqual(captured_inputs[0].content, "You are a careful health assistant.")
            user_messages = [message for message in captured_inputs if message.role == "user"]
            self.assertEqual([message.content for message in user_messages], ["Hello target"])

    async def test_run_inference_rejects_item_tools_without_simulator_target(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {
                "description": "seed prompt",
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Fetch account data.",
                        "parameters": [
                            {"name": "account_id", "type": "string", "description": "Customer account id."}
                        ],
                    }
                ],
            },
        }

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "test case tools are only allowed when tool_source=per_test_case",
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

    async def test_run_inference_per_test_case_uses_seed_tools_with_simulator_target(self) -> None:
        test_case_row = {
            "type": "prompt",
            "test_case_id": "test-case-1",
            "seed": {
                "description": "seed prompt",
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Fetch account data.",
                        "parameters": [
                            {"name": "account_id", "type": "string", "description": "Customer account id."}
                        ],
                    }
                ],
            },
        }
        captured_test_case_payload: dict[str, object] = {}

        async def fake_run_prompt_test_case(**kwargs):
            captured_test_case_payload.update(kwargs["test_case"]["seed"])

            class FakeTranscript:
                def to_dict(self) -> dict[str, object]:
                    return {"type": "prompt", "test_case_id": str(kwargs["test_case"]["test_case_id"])}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_row) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4", tools=ToolsConfig(simulator="azure/gpt-5.4-mini")),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

        self.assertEqual(captured_test_case_payload["tools"][0]["name"], "lookup")

    async def test_run_inference_preserves_input_order_under_parallel_completion(self) -> None:
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "slow prompt"}},
            {"type": "prompt", "seed": {"description": "fast prompt"}},
        ]

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = kwargs["test_case"]["test_case_id"]
            if test_case_id == "test_case_000001":
                await asyncio.sleep(0.05)

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": test_case_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text("\n".join(json.dumps(row) for row in test_case_rows) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(concurrency=2),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(sorted(row["test_case_id"] for row in inference_rows), ["test_case_000001", "test_case_000002"])

    async def test_run_inference_writes_transcripts_incrementally_before_all_workers_finish(self) -> None:
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "slow prompt"}},
            {"type": "prompt", "seed": {"description": "fast prompt"}},
        ]
        release_slow = asyncio.Event()
        fast_finished = asyncio.Event()

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = str(kwargs["test_case"]["test_case_id"])
            if test_case_id == "test_case_000001":
                await release_slow.wait()
            else:
                fast_finished.set()

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": test_case_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            inference_set_path = out_dir / "inference_set.jsonl"
            test_set_path.write_text("\n".join(json.dumps(row) for row in test_case_rows) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                inference_task = asyncio.create_task(
                    run_inference(
                        test_set_path=str(test_set_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            inference=InferenceConfig(concurrency=2),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-inference",
                    )
                )

                await asyncio.wait_for(fast_finished.wait(), timeout=1)
                for _ in range(50):
                    if inference_set_path.exists() and inference_set_path.read_text(encoding="utf-8").strip():
                        break
                    await asyncio.sleep(0.01)

                interim_rows = [
                    json.loads(line)
                    for line in inference_set_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual([row["test_case_id"] for row in interim_rows], ["test_case_000002"])

                release_slow.set()
                await inference_task

            final_rows = [
                json.loads(line)
                for line in inference_set_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(sorted(row["test_case_id"] for row in final_rows), ["test_case_000001", "test_case_000002"])

    async def test_run_inference_keeps_partial_successful_transcripts_when_later_worker_fails(self) -> None:
        """A per-row worker failure no longer kills the stage outright.

        With the resilience fix in place, the inference stage tolerates
        per-row failures so long as at least one seed produced a useful
        transcript and the failure rate is below the configured
        threshold. The successful transcript stays on disk and the
        stage returns with ``errored_count`` reflecting the failure.
        Re-running the same suite picks up the failed seed via the
        existing resume logic (it never made it into inference_set.jsonl).

        The ``ASSERT_INFERENCE_ERROR_FAIL_RATIO`` override is needed because
        a 2-seed test where 1 seed fails has a 50% error rate, which
        exceeds the 10% production default. The override keeps the test
        focused on the soft-fail contract; the ratio threshold itself
        is covered by a dedicated test below.
        """
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "successful prompt"}},
            {"type": "prompt", "seed": {"description": "failing prompt"}},
        ]
        release_failure = asyncio.Event()
        success_finished = asyncio.Event()

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = str(kwargs["test_case"]["test_case_id"])
            if test_case_id == "test_case_000001":
                success_finished.set()

                class FakeTranscript:
                    def to_dict(self_inner) -> dict[str, str]:
                        return {"type": "prompt", "test_case_id": test_case_id}

                return FakeTranscript()

            await release_failure.wait()
            raise RuntimeError("boom")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            inference_set_path = out_dir / "inference_set.jsonl"
            test_set_path.write_text("\n".join(json.dumps(row) for row in test_case_rows) + "\n", encoding="utf-8")

            with patch.dict(os.environ, {"ASSERT_INFERENCE_ERROR_FAIL_RATIO": "0.6"}, clear=False), \
                 patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                inference_task = asyncio.create_task(
                    run_inference(
                        test_set_path=str(test_set_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            inference=InferenceConfig(concurrency=2),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-inference",
                    )
                )

                await asyncio.wait_for(success_finished.wait(), timeout=1)
                for _ in range(50):
                    if inference_set_path.exists() and inference_set_path.read_text(encoding="utf-8").strip():
                        break
                    await asyncio.sleep(0.01)

                interim_rows = [
                    json.loads(line)
                    for line in inference_set_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual([row["test_case_id"] for row in interim_rows], ["test_case_000001"])

                release_failure.set()
                result = await inference_task

            final_rows = [
                json.loads(line)
                for line in inference_set_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual([row["test_case_id"] for row in final_rows], ["test_case_000001"])
        self.assertEqual(result["errored_count"], 1)
        self.assertEqual(result["new_count"], 2)
        self.assertEqual(result["target_error_count"], 0)

    async def test_run_inference_fails_when_all_seeds_error_and_no_cache(self) -> None:
        """If every seed errors and nothing was previously cached, the
        stage still fails — that's a systemic problem (auth, config,
        broken target) rather than per-row noise, and silently
        completing would be misleading.
        """
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "first failing prompt"}},
            {"type": "prompt", "seed": {"description": "second failing prompt"}},
        ]

        async def fake_run_prompt_test_case(**kwargs):
            raise RuntimeError("boom")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text("\n".join(json.dumps(row) for row in test_case_rows) + "\n", encoding="utf-8")

            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    await run_inference(
                        test_set_path=str(test_set_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            inference=InferenceConfig(concurrency=2),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-inference",
                    )

    async def test_run_inference_tolerates_one_pct_failures_when_total_is_large(self) -> None:
        """Total >= 20 and <=10% failures: log warning, continue to judge.

        Mirrors the real production scenario where one tester turn out
        of ~100 trips Azure's content filter on an adversarial test case.
        """
        test_case_rows = [
            {"type": "prompt", "seed": {"description": f"prompt-{i:03d}"}}
            for i in range(20)
        ]

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = str(kwargs["test_case"]["test_case_id"])
            # Fail exactly one of 20 (5% — under 10% threshold).
            if test_case_id == "test_case_000010":
                raise RuntimeError("content_filter_blocked")

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": test_case_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            with patch(
                "assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case
            ):
                # Should NOT raise — the single failure is tolerated.
                result = await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(concurrency=4),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference-tolerated",
                )

            self.assertEqual(result["count"], 20)
            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(inference_rows), 19)
            self.assertNotIn(
                "test_case_000010",
                {row["test_case_id"] for row in inference_rows},
            )

    async def test_run_inference_raises_when_failures_exceed_tolerance(self) -> None:
        """Total >= 20 with > 10% failures still raises: real outage."""
        test_case_rows = [
            {"type": "prompt", "seed": {"description": f"prompt-{i:03d}"}}
            for i in range(20)
        ]

        async def fake_run_prompt_test_case(**kwargs):
            # Fail 5/20 = 25% — over 10% threshold (tolerance is 2).
            test_case_id = str(kwargs["test_case"]["test_case_id"])
            if test_case_id in {f"test_case_{i:06d}" for i in range(1, 6)}:
                raise RuntimeError("deployment_not_found")

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": test_case_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            with patch(
                "assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "deployment_not_found"
                ):
                    await run_inference(
                        test_set_path=str(test_set_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            inference=InferenceConfig(concurrency=4),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-inference-fatal",
                    )

    async def test_run_inference_resumes_from_existing_transcripts(self) -> None:
        """Pre-populated inference_set.jsonl causes completed test_set to be skipped."""
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "already done"}},
            {"type": "prompt", "seed": {"description": "still pending"}},
        ]
        call_log: list[str] = []

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = str(kwargs["test_case"]["test_case_id"])
            call_log.append(test_case_id)

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": test_case_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            out_dir.mkdir()
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            # First run: let both test_set complete.
            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                result = await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    save_dir=str(out_dir),
                    run_id="run-resume",
                )
            self.assertEqual(result["count"], 2)
            self.assertEqual(sorted(call_log), ["test_case_000001", "test_case_000002"])

            # Second run: test_case_000001 and test_case_000002 already exist — nothing to do.
            call_log.clear()
            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                result = await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    save_dir=str(out_dir),
                    run_id="run-resume",
                )
            self.assertEqual(call_log, [], "No test_set should have been re-run")
            self.assertEqual(result["count"], 2)

    async def test_run_inference_discards_transcripts_on_config_change(self) -> None:
        """Changing target model invalidates existing transcripts."""
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "a prompt"}},
        ]

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = str(kwargs["test_case"]["test_case_id"])

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": test_case_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(json.dumps(test_case_rows[0]) + "\n", encoding="utf-8")

            # First run with model A.
            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/model-a"),
                    save_dir=str(out_dir),
                    run_id="run-cfg",
                )

            # Second run with model B — should discard and re-run.
            with patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case):
                result = await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/model-b"),
                    save_dir=str(out_dir),
                    run_id="run-cfg",
                )
            # count is 1 (re-ran, not 2 = resumed + new)
            self.assertEqual(result["count"], 1)
        test_case_rows = [
            {"type": "prompt", "seed": {"description": "base prompt"}},
            {
                "type": "scenario",
                "test_case_id": "scenario-base",
                "seed": {"title": "Scenario", "description": "scenario description"},
            },
            {
                "type": "scenario",
                "test_case_id": "scenario-second",
                "seed": {"title": "Second scenario", "description": "second description"},
            },
        ]

        async def fake_run_prompt_test_case(**kwargs):
            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "prompt", "test_case_id": str(kwargs["test_case"]["test_case_id"])}

            return FakeTranscript()

        async def fake_run_scenario_test_case(**kwargs):
            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"type": "scenario", "test_case_id": str(kwargs["test_case"]["test_case_id"])}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text("\n".join(json.dumps(row) for row in test_case_rows) + "\n", encoding="utf-8")

            with (
                patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case),
                patch("assert_ai.stages.inference._run_scenario_test_case", new=fake_run_scenario_test_case),
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(max_turns=1, concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-inference",
                )

            canonical_rows = load_test_cases(test_set_path)

        self.assertEqual(
            [row["test_case_id"] for row in canonical_rows],
            ["test_case_000001", "test_case_000002", "test_case_000003"],
        )
        self.assertNotIn("parent_test_case_id", canonical_rows[2])

    # ------------------------------------------------------------------
    # Per-row refusal isolation (absorbed from PR #44)
    # ------------------------------------------------------------------

    async def test_run_prompt_test_case_records_target_input_refusal(self) -> None:
        """Target-side LLMInputError is recorded as a transcript event, not raised.

        Without this, one Azure content-filter refusal kills the entire
        batch even though hundreds of other adversarial prompts completed
        successfully. Absorbed from PR #44 commit 82cf339.
        """
        from assert_ai.core.session import HostedSession

        async def fake_run_turn(self_inner, messages):
            raise LLMInputError(
                "Bad request: AzureException BadRequestError - Invalid prompt: "
                "your prompt was flagged as potentially violating our usage taxonomy."
            )

        async def fake_open(self_inner):
            return None

        async def fake_close(self_inner):
            return None

        test_case_row = {
            "type": "prompt",
            "test_case_id": "test_case_000001",
            "seed": {"description": "adversarial prompt the target refuses"},
        }

        with (
            patch.object(HostedSession, "open", new=fake_open),
            patch.object(HostedSession, "close", new=fake_close),
            patch.object(HostedSession, "run_turn", new=fake_run_turn),
        ):
            transcript = await _run_prompt_test_case(
                test_case=test_case_row,
                target=TargetConfig(model="azure/gpt-5.4-mini"),
                inference=InferenceConfig(max_turns=1, concurrency=1),
                max_tokens=1000,
                config_path=None,
            )

        self.assertEqual(transcript.stop_reason, "target_input_refused")
        refusal_events = [
            event for event in transcript.events
            if event.edit.type == "add_message"
            and "[TARGET INPUT REFUSED:" in (event.edit.message.content or "")
        ]
        self.assertEqual(len(refusal_events), 1)
        self.assertIn(
            "flagged as potentially violating",
            refusal_events[0].edit.message.content,
        )

    async def test_run_inference_isolates_target_input_refusal_to_one_seed(self) -> None:
        """One target_input_refused seed must not abort the rest of the batch.

        End-to-end check that the target-side refusal handling produces
        a transcript per seed and that the batch returns successfully.
        Absorbed from PR #44 commit 82cf339.
        """
        from assert_ai.core.session import HostedSession

        test_case_rows = [
            {"type": "prompt", "seed": {"description": f"prompt {i}"}}
            for i in range(5)
        ]
        run_turn_calls: list[str] = []

        async def fake_run_turn(self_inner, messages):
            user_text = next(
                (m.text or "" for m in messages if m.role == "user"),
                "",
            )
            run_turn_calls.append(user_text)
            if "prompt 2" in user_text:
                raise LLMInputError(
                    "Bad request: AzureException BadRequestError - "
                    "prompt flagged as potentially violating our usage taxonomy"
                )
            return TurnResult(
                text="OK",
                state_messages=list(messages) + [Message(role="assistant", content="OK")],
                interaction_messages=[
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": "OK"},
                ],
                raw={"response": {"content": "OK"}},
            )

        async def fake_open(self_inner):
            return None

        async def fake_close(self_inner):
            return None

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch.object(HostedSession, "open", new=fake_open),
                patch.object(HostedSession, "close", new=fake_close),
                patch.object(HostedSession, "run_turn", new=fake_run_turn),
            ):
                result = await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4-mini"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-refusal-isolation",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(run_turn_calls), 5)
        self.assertEqual(len(inference_rows), 5)
        self.assertEqual(result["new_count"], 5)

        by_test_case = {row["test_case_id"]: row for row in inference_rows}
        refused = by_test_case["test_case_000003"]
        self.assertEqual(refused["stop_reason"], "target_input_refused")
        refusal_events = [
            event for event in refused["events"]
            if event["edit"]["type"] == "add_message"
            and "[TARGET INPUT REFUSED:" in event["edit"]["message"].get("content", "")
        ]
        self.assertEqual(len(refusal_events), 1)

        for completed_id in ("test_case_000001", "test_case_000002", "test_case_000004", "test_case_000005"):
            self.assertEqual(
                by_test_case[completed_id]["stop_reason"],
                "completed",
                f"{completed_id} should have completed cleanly",
            )

    async def test_run_inference_isolates_tester_input_refusal_to_one_seed(self) -> None:
        """One tester_input_refused seed must not abort the rest of the batch.

        Absorbed from PR #44 commit f265154. The tester's adversarial
        system prompt is exactly the kind of input Azure Prompt Shields'
        jailbreak detector flags. The fix routes tester-side input
        errors into a recorded transcript event with
        stop_reason='tester_input_refused'.
        """
        test_case_rows = [
            {
                "type": "scenario",
                "seed": {
                    "title": f"Title {i}",
                    "description": f"scenario seed {i}",
                },
            }
            for i in range(5)
        ]
        tester_calls: list[str] = []

        async def fake_generate(model, messages, options):
            del options
            description_marker = ""
            for msg in messages:
                content = msg.content if hasattr(msg, "content") else msg.get("content", "")
                if "scenario seed" in content:
                    description_marker = content
                    break
            tester_calls.append(description_marker[:120])
            if "scenario seed 2" in description_marker:
                raise LLMInputError(
                    "Bad request: AzureException BadRequestError - Invalid "
                    "prompt: your prompt was flagged as potentially "
                    "violating our usage taxonomy (Prompt Shields jailbreak)"
                )
            return ModelResponse(text="Where should I go?", model=str(model))

        class FakeHostedSession:
            runtime_mode = "hosted"

            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, messages):
                user_text = ""
                for msg in reversed(messages):
                    if msg.role == "user":
                        user_text = msg.text
                        break
                return TurnResult(
                    text="OK.",
                    state_messages=list(messages) + [Message(role="assistant", content="OK.")],
                    interaction_messages=[
                        {"role": "user", "content": user_text},
                        {"role": "assistant", "content": "OK."},
                    ],
                    raw={"response": {"content": "OK."}},
                )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch("assert_ai.stages.inference.generate", new=fake_generate),
                patch("assert_ai.stages.inference._build_target_session", return_value=FakeHostedSession()),
            ):
                result = await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4-mini"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4-mini"),
                        inference=InferenceConfig(max_turns=1, concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-tester-refusal",
                )

            inference_rows = [
                json.loads(line)
                for line in (out_dir / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(tester_calls), 5)
        self.assertEqual(len(inference_rows), 5)
        self.assertEqual(result["new_count"], 5)

        by_test_case = {row["test_case_id"]: row for row in inference_rows}
        refused = by_test_case["test_case_000003"]
        self.assertEqual(refused["stop_reason"], "tester_input_refused")
        refusal_events = [
            event for event in refused["events"]
            if event["edit"]["type"] == "add_message"
            and "[TESTER INPUT REFUSED:" in event["edit"]["message"].get("content", "")
        ]
        self.assertEqual(len(refusal_events), 1)

    async def test_run_inference_still_fails_fast_on_provider_5xx(self) -> None:
        """LLMProviderError (Azure 5xx) is global, not test-case-specific.

        Verifies the per-row tolerance didn't accidentally swallow
        auth/rate-limit/5xx errors that should still abort the stage.
        Absorbed from PR #44 commit 82cf339.
        """
        test_case_rows = [
            {"type": "prompt", "seed": {"description": f"prompt {i}"}}
            for i in range(3)
        ]

        async def fake_run_prompt_test_case(**kwargs):
            raise LLMProviderError("Azure 503 ServiceUnavailable")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case),
                self.assertRaises(LLMProviderError),
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4-mini"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        tester=TesterConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-provider-error",
                )

    async def test_run_inference_fails_when_untyped_error_ratio_exceeds_threshold(self) -> None:
        """Untyped errors above the failure threshold abort the stage.

        Per-row tolerance is for typed refusals (target_input_refused,
        tester_input_refused, target_error). When the worker hits
        unrecognised exceptions at scale we should fail loudly because
        that's almost always a systemic problem (config bug, broken
        validation) rather than test-case-specific bad luck. Default
        threshold is 10%; this test forces 50% (1/2) and asserts the
        stage raises. The companion test
        ``test_run_inference_keeps_partial_successful_transcripts_when_later_worker_fails``
        covers the soft-fail path with the threshold relaxed.
        """
        test_case_rows = [
            {"type": "prompt", "seed": {"description": f"prompt {i}"}}
            for i in range(2)
        ]

        async def fake_run_prompt_test_case(**kwargs):
            test_case_id = str(kwargs["test_case"]["test_case_id"])
            if test_case_id == "test_case_000001":
                class FakeTranscript:
                    def to_dict(self_inner) -> dict[str, str]:
                        return {"type": "prompt", "test_case_id": test_case_id}

                return FakeTranscript()
            raise RuntimeError("worker exploded")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_set_path = tmp_path / "test_set.jsonl"
            out_dir = tmp_path / "run"
            test_set_path.write_text(
                "\n".join(json.dumps(row) for row in test_case_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {"ASSERT_INFERENCE_ERROR_FAIL_RATIO": "0.10"}, clear=False),
                patch("assert_ai.stages.inference._run_prompt_test_case", new=fake_run_prompt_test_case),
                self.assertRaisesRegex(RuntimeError, "worker exploded"),
            ):
                await run_inference(
                    test_set_path=str(test_set_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        inference=InferenceConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-error-ratio",
                )


if __name__ == "__main__":
    unittest.main()
