import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from p2m.core.config_model import AuditorConfig, EvaluationConfig, JudgeConfig, RolloutConfig, TargetConfig, ToolsConfig
from p2m.core.io import load_seeds
from p2m.core.model_client import LLMInputError, LLMProviderError, Message, ModelResponse
from p2m.core.session import TurnResult
from p2m.stages.rollout import _prepare_seeds, _rollout_config_fingerprint, _run_prompt_seed, run_rollout
from p2m.viewer_read_model import ViewerReadModelBuildError


class RolloutStageTest(unittest.IsolatedAsyncioTestCase):
    def test_rollout_config_fingerprint_changes_with_seeds_content(self) -> None:
        """Fingerprint must include seed content so regenerating seeds invalidates cached transcripts.

        Without this, the resume path keys on seed_id alone, and seed ids
        are deterministic enough that fresh seeds collide with cached
        transcripts. Regression test for the Apr 28 cache-invalidation fix.
        """
        target = TargetConfig(model="azure/gpt-5.4")
        evaluation = EvaluationConfig(
            rollout=RolloutConfig(max_turns=4, concurrency=1),
            judge=JudgeConfig(model="azure/gpt-5.4"),
            auditor=AuditorConfig(model="azure/gpt-5.4"),
        )
        with TemporaryDirectory() as tmp_dir:
            seeds_a = Path(tmp_dir) / "seeds_a.jsonl"
            seeds_b = Path(tmp_dir) / "seeds_b.jsonl"
            seeds_a.write_text(
                '{"kind":"prompt","seed_id":"prompt-x-001","content":"first"}\n',
                encoding="utf-8",
            )
            seeds_b.write_text(
                '{"kind":"prompt","seed_id":"prompt-x-001","content":"second"}\n',
                encoding="utf-8",
            )

            hash_no_seeds = _rollout_config_fingerprint(target, evaluation, 1024)
            hash_a = _rollout_config_fingerprint(target, evaluation, 1024, seeds_path=seeds_a)
            hash_b = _rollout_config_fingerprint(target, evaluation, 1024, seeds_path=seeds_b)

        # Including seeds_path must materially change the fingerprint
        # versus the legacy no-seeds form, and two different seed files
        # with the same seed_id but different content must hash apart.
        self.assertNotEqual(hash_a, hash_no_seeds)
        self.assertNotEqual(hash_a, hash_b)

    def test_prepare_seeds_rejects_non_empty_seed_prompt_when_target_prompt_is_fixed(self) -> None:
        rows = [
            {
                "kind": "prompt",
                "seed": {
                    "description": "seed prompt",
                    "system_prompt": "per-seed prompt",
                },
            }
        ]
        with self.assertRaisesRegex(
            ValueError,
            "target.system_prompt cannot be combined with non-empty seed.system_prompt",
        ):
            _prepare_seeds(
                rows,
                tool_source="runtime",
                fixed_system_prompt="fixed prompt",
            )

    def test_prepare_seeds_treats_empty_seed_prompt_as_absent(self) -> None:
        rows = [
            {
                "kind": "prompt",
                "seed": {
                    "description": "seed prompt",
                    "system_prompt": "   ",
                },
            }
        ]
        seeds = _prepare_seeds(
            rows,
            tool_source="runtime",
            fixed_system_prompt=None,
        )

        self.assertNotIn("system_prompt", seeds[0]["seed"])

    def test_prepare_seeds_validates_per_seed_tools(self) -> None:
        rows = [
            {
                "kind": "prompt",
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
        seeds = _prepare_seeds(
            rows,
            tool_source="per_seed",
            fixed_system_prompt=None,
        )

        self.assertEqual(seeds[0]["seed"]["tools"][0]["name"], "lookup")

    def test_prepare_seeds_rejects_seed_tools_for_runtime_tool_source(self) -> None:
        rows = [
            {
                "kind": "prompt",
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
        with self.assertRaisesRegex(ValueError, "seed.tools is only allowed when tool_source=per_seed"):
            _prepare_seeds(
                rows,
                tool_source="runtime",
                fixed_system_prompt=None,
            )

    async def test_run_rollout_uses_fixed_target_prompt_exactly(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
            "seed": {"description": "seed prompt"},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4", system_prompt="You are a coding agent."),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

        self.assertEqual(captured_messages[0].role, "system")
        self.assertEqual(captured_messages[0].content, "You are a coding agent.")
        self.assertEqual(captured_messages[1].content, "seed prompt")

    async def test_run_rollout_uses_per_seed_prompt_exactly(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
            "seed": {
                "description": "seed prompt",
                "system_prompt": "Per-seed prompt",
            },
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

        self.assertEqual(captured_messages[0].role, "system")
        self.assertEqual(captured_messages[0].content, "Per-seed prompt")

    async def test_run_rollout_can_leave_versioned_seed_file_unchanged(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "original-seed-id",
            "seed": {"description": "seed prompt"},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            original_seed_text = json.dumps(seed_row) + "\n"
            seed_path.write_text(original_seed_text, encoding="utf-8")

            with patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                    rewrite_seed_path=False,
                )

            self.assertEqual(seed_path.read_text(encoding="utf-8"), original_seed_text)

    async def test_run_rollout_never_mutates_versioned_seed_artifact(self) -> None:
        """Even when callers pass rewrite_seed_path=True, files under
        artifacts/seeds/v#### must be left intact so the cached file_hashes
        stay valid."""

        seed_row = {
            "kind": "prompt",
            "seed_id": "original-seed-id",
            "seed": {"description": "seed prompt"},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            versioned_dir = tmp_path / "suite" / "artifacts" / "seeds" / "v0001"
            versioned_dir.mkdir(parents=True, exist_ok=True)
            seed_path = versioned_dir / "seeds.jsonl"
            out_dir = tmp_path / "run"
            original_seed_text = json.dumps(seed_row) + "\n"
            seed_path.write_text(original_seed_text, encoding="utf-8")

            with patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                    rewrite_seed_path=True,
                )

            self.assertEqual(seed_path.read_text(encoding="utf-8"), original_seed_text)

    async def test_run_rollout_fails_when_viewer_artifact_build_fails(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
            "seed": {"description": "seed prompt"},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with (
                patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()),
                patch(
                    "p2m.stages.rollout.build_run_viewer_artifacts",
                    side_effect=ViewerReadModelBuildError("viewer build failed"),
                ),
            ):
                with self.assertRaisesRegex(ViewerReadModelBuildError, "viewer build failed"):
                    await run_rollout(
                        seed_path=str(seed_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                        save_dir=str(out_dir),
                        run_id="run-rollout",
                    )

    async def test_run_rollout_persists_owned_llm_calls_and_links_message_ids(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
            "seed": {"description": "seed prompt"},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            [transcript_row] = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(transcript_row["llm_calls"][0]["source"], "target")
        self.assertEqual(transcript_row["llm_calls"][0]["request"]["model"], "azure/gpt-5.4")
        self.assertEqual(transcript_row["llm_calls"][0]["response"]["id"], "resp_1")
        self.assertEqual(transcript_row["llm_calls"][0]["message_ids"], ["event:1"])

    async def test_run_rollout_sets_runtime_close_error_stop_reason(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
            "seed": {"description": "seed prompt"},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._build_hosted_session", return_value=FakeSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(transcript_rows[0]["stop_reason"], "runtime_close_error")

    async def test_run_rollout_external_transcript_writes_minimal_rows(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
            "seed": {"description": "Please help", "system_prompt": "You are a health assistant."},
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout.ExternalSession", return_value=FakeExternalSession()):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(connector="examples.agents.demo"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertNotIn("metadata", transcript_rows[0])
        self.assertNotIn("run_id", transcript_rows[0])
        self.assertEqual(transcript_rows[0]["events"][0]["edit"]["message"]["content"], "You are a health assistant.")

    async def test_run_rollout_scenario_keeps_setup_hidden_for_external_target(self) -> None:
        seed_row = {
            "kind": "scenario",
            "seed_id": "seed-1",
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with (
                patch("p2m.stages.rollout.generate", new=fake_generate),
                patch("p2m.stages.rollout.ExternalSession", return_value=FakeExternalSession()),
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(connector="examples.agents.demo"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(max_turns=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        target_events = [
            event
            for event in transcript_rows[0]["events"]
            if "target" in event["view"] and event["edit"]["type"] == "add_message"
        ]
        self.assertEqual(target_events[0]["edit"]["message"]["role"], "user")
        self.assertEqual(target_events[0]["edit"]["message"]["content"], "Hello target")
        self.assertEqual(transcript_rows[0]["events"][0]["edit"]["type"], "set_system_message")
        self.assertEqual(
            transcript_rows[0]["events"][0]["edit"]["message"]["content"],
            "You are a careful health assistant.",
        )

    async def test_run_rollout_scenario_forwards_setup_like_text_unchanged(self) -> None:
        seed_row = {
            "kind": "scenario",
            "seed_id": "seed-1",
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with (
                patch("p2m.stages.rollout.generate", new=fake_generate),
                patch("p2m.stages.rollout.ExternalSession", return_value=FakeExternalSession()),
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(connector="examples.agents.demo"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(max_turns=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        target_events = [
            event
            for event in transcript_rows[0]["events"]
            if "target" in event["view"] and event["edit"]["type"] == "add_message"
        ]
        # With effective_system_message, the auditor's first attempt ("Be a careful
        # health assistant for Mara.") is detected as hidden setup and retried.
        # The second auditor response is forwarded to the external target.
        self.assertEqual(target_events[0]["edit"]["message"]["content"], "hey, can you help me figure out this headache?")

    async def test_run_rollout_scenario_keeps_setup_hidden_for_hosted_target(self) -> None:
        seed_row = {
            "kind": "scenario",
            "seed_id": "seed-1",
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with (
                patch("p2m.stages.rollout.generate", new=fake_generate),
                patch("p2m.stages.rollout._build_hosted_session", return_value=FakeHostedSession()),
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(max_turns=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

        self.assertEqual(captured_inputs[0].role, "system")
        self.assertEqual(captured_inputs[0].content, "You are a careful health assistant.")
        user_messages = [message for message in captured_inputs if message.role == "user"]
        self.assertEqual([message.content for message in user_messages], ["Hello target"])

    async def test_run_rollout_scenario_keeps_setup_hidden_for_other_hosted_runtime_modes(self) -> None:
        seed_row = {
            "kind": "scenario",
            "seed_id": "seed-1",
            "concept": "Risk",
            "factors": {"behavior": "behavior-a"},
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
                seed_path = tmp_path / "seeds.jsonl"
                out_dir = tmp_path / "run"
                seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

                with (
                    patch("p2m.stages.rollout.generate", new=fake_generate),
                    patch("p2m.stages.rollout._build_hosted_session", return_value=FakeHostedSession()),
                ):
                    await run_rollout(
                        seed_path=str(seed_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            auditor=AuditorConfig(model="azure/gpt-5.4"),
                            rollout=RolloutConfig(max_turns=1),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-rollout",
                    )

            self.assertEqual(captured_inputs[0].role, "system")
            self.assertEqual(captured_inputs[0].content, "You are a careful health assistant.")
            user_messages = [message for message in captured_inputs if message.role == "user"]
            self.assertEqual([message.content for message in user_messages], ["Hello target"])

    async def test_run_rollout_rejects_item_tools_without_simulator_target(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "seed.tools is only allowed when tool_source=per_seed",
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4")),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

    async def test_run_rollout_per_seed_uses_seed_tools_with_simulator_target(self) -> None:
        seed_row = {
            "kind": "prompt",
            "seed_id": "seed-1",
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
        captured_seed_payload: dict[str, object] = {}

        async def fake_run_prompt_seed(**kwargs):
            captured_seed_payload.update(kwargs["seed"]["seed"])

            class FakeTranscript:
                def to_dict(self) -> dict[str, object]:
                    return {"kind": "prompt", "seed_id": str(kwargs["seed"]["seed_id"])}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_row) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4", tools=ToolsConfig(simulator="azure/gpt-5.4-mini")),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

        self.assertEqual(captured_seed_payload["tools"][0]["name"], "lookup")

    async def test_run_rollout_preserves_input_order_under_parallel_completion(self) -> None:
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "slow prompt"}},
            {"kind": "prompt", "seed": {"description": "fast prompt"}},
        ]

        async def fake_run_prompt_seed(**kwargs):
            seed_id = kwargs["seed"]["seed_id"]
            if seed_id == "seed_000001":
                await asyncio.sleep(0.05)

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": seed_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text("\n".join(json.dumps(row) for row in seed_rows) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(concurrency=2),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(sorted(row["seed_id"] for row in transcript_rows), ["seed_000001", "seed_000002"])

    async def test_run_rollout_writes_transcripts_incrementally_before_all_workers_finish(self) -> None:
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "slow prompt"}},
            {"kind": "prompt", "seed": {"description": "fast prompt"}},
        ]
        release_slow = asyncio.Event()
        fast_finished = asyncio.Event()

        async def fake_run_prompt_seed(**kwargs):
            seed_id = str(kwargs["seed"]["seed_id"])
            if seed_id == "seed_000001":
                await release_slow.wait()
            else:
                fast_finished.set()

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": seed_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            transcripts_path = out_dir / "transcripts.jsonl"
            seed_path.write_text("\n".join(json.dumps(row) for row in seed_rows) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                rollout_task = asyncio.create_task(
                    run_rollout(
                        seed_path=str(seed_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            rollout=RolloutConfig(concurrency=2),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-rollout",
                    )
                )

                await asyncio.wait_for(fast_finished.wait(), timeout=1)
                for _ in range(50):
                    if transcripts_path.exists() and transcripts_path.read_text(encoding="utf-8").strip():
                        break
                    await asyncio.sleep(0.01)

                interim_rows = [
                    json.loads(line)
                    for line in transcripts_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual([row["seed_id"] for row in interim_rows], ["seed_000002"])

                release_slow.set()
                await rollout_task

            final_rows = [
                json.loads(line)
                for line in transcripts_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(sorted(row["seed_id"] for row in final_rows), ["seed_000001", "seed_000002"])

    async def test_run_rollout_keeps_partial_successful_transcripts_when_later_worker_fails(self) -> None:
        """A per-row worker failure no longer kills the stage outright.

        With the resilience fix in place, the rollout stage tolerates
        per-row failures so long as at least one seed produced a useful
        transcript and the failure rate is below the configured
        threshold. The successful transcript stays on disk and the
        stage returns with ``errored_count`` reflecting the failure.
        Re-running the same suite picks up the failed seed via the
        existing resume logic (it never made it into transcripts.jsonl).

        The ``P2M_ROLLOUT_ERROR_FAIL_RATIO`` override is needed because
        a 2-seed test where 1 seed fails has a 50% error rate, which
        exceeds the 10% production default. The override keeps the test
        focused on the soft-fail contract; the ratio threshold itself
        is covered by a dedicated test below.
        """
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "successful prompt"}},
            {"kind": "prompt", "seed": {"description": "failing prompt"}},
        ]
        release_failure = asyncio.Event()
        success_finished = asyncio.Event()

        async def fake_run_prompt_seed(**kwargs):
            seed_id = str(kwargs["seed"]["seed_id"])
            if seed_id == "seed_000001":
                success_finished.set()

                class FakeTranscript:
                    def to_dict(self_inner) -> dict[str, str]:
                        return {"kind": "prompt", "seed_id": seed_id}

                return FakeTranscript()

            await release_failure.wait()
            raise RuntimeError("boom")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            transcripts_path = out_dir / "transcripts.jsonl"
            seed_path.write_text("\n".join(json.dumps(row) for row in seed_rows) + "\n", encoding="utf-8")

            with patch.dict(os.environ, {"P2M_ROLLOUT_ERROR_FAIL_RATIO": "0.6"}, clear=False), \
                 patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                rollout_task = asyncio.create_task(
                    run_rollout(
                        seed_path=str(seed_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            rollout=RolloutConfig(concurrency=2),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-rollout",
                    )
                )

                await asyncio.wait_for(success_finished.wait(), timeout=1)
                for _ in range(50):
                    if transcripts_path.exists() and transcripts_path.read_text(encoding="utf-8").strip():
                        break
                    await asyncio.sleep(0.01)

                interim_rows = [
                    json.loads(line)
                    for line in transcripts_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual([row["seed_id"] for row in interim_rows], ["seed_000001"])

                release_failure.set()
                result = await rollout_task

            final_rows = [
                json.loads(line)
                for line in transcripts_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual([row["seed_id"] for row in final_rows], ["seed_000001"])
        self.assertEqual(result["errored_count"], 1)
        self.assertEqual(result["new_count"], 2)
        self.assertEqual(result["target_error_count"], 0)

    async def test_run_rollout_fails_when_all_seeds_error_and_no_cache(self) -> None:
        """If every seed errors and nothing was previously cached, the
        stage still fails — that's a systemic problem (auth, config,
        broken target) rather than per-row noise, and silently
        completing would be misleading.
        """
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "first failing prompt"}},
            {"kind": "prompt", "seed": {"description": "second failing prompt"}},
        ]

        async def fake_run_prompt_seed(**kwargs):
            raise RuntimeError("boom")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text("\n".join(json.dumps(row) for row in seed_rows) + "\n", encoding="utf-8")

            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    await run_rollout(
                        seed_path=str(seed_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            rollout=RolloutConfig(concurrency=2),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-rollout",
                    )

    async def test_run_rollout_tolerates_one_pct_failures_when_total_is_large(self) -> None:
        """Total >= 20 and ≤10% failures: log warning, continue to judge.

        Mirrors the real production scenario where one auditor turn out
        of ~100 trips Azure's content filter on an adversarial seed.
        """
        seed_rows = [
            {"kind": "prompt", "seed": {"description": f"prompt-{i:03d}"}}
            for i in range(20)
        ]

        async def fake_run_prompt_seed(**kwargs):
            seed_id = str(kwargs["seed"]["seed_id"])
            # Fail exactly one of 20 (5% — under 10% threshold).
            if seed_id == "seed_000010":
                raise RuntimeError("content_filter_blocked")

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": seed_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            with patch(
                "p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed
            ):
                # Should NOT raise — the single failure is tolerated.
                result = await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(concurrency=4),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout-tolerated",
                )

            self.assertEqual(result["count"], 20)
            transcripts = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(transcripts), 19)
            self.assertNotIn(
                "seed_000010",
                {row["seed_id"] for row in transcripts},
            )

    async def test_run_rollout_raises_when_failures_exceed_tolerance(self) -> None:
        """Total >= 20 with > 10% failures still raises: real outage."""
        seed_rows = [
            {"kind": "prompt", "seed": {"description": f"prompt-{i:03d}"}}
            for i in range(20)
        ]

        async def fake_run_prompt_seed(**kwargs):
            # Fail 5/20 = 25% — over 10% threshold (tolerance is 2).
            seed_id = str(kwargs["seed"]["seed_id"])
            if seed_id in {f"seed_{i:06d}" for i in range(5)}:
                raise RuntimeError("deployment_not_found")

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": seed_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            with patch(
                "p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "deployment_not_found"
                ):
                    await run_rollout(
                        seed_path=str(seed_path),
                        target=TargetConfig(model="azure/gpt-5.4"),
                        evaluation=EvaluationConfig(
                            judge=JudgeConfig(model="azure/gpt-5.4"),
                            rollout=RolloutConfig(concurrency=4),
                        ),
                        save_dir=str(out_dir),
                        run_id="run-rollout-fatal",
                    )

    async def test_run_rollout_resumes_from_existing_transcripts(self) -> None:
        """Pre-populated transcripts.jsonl causes completed seeds to be skipped."""
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "already done"}},
            {"kind": "prompt", "seed": {"description": "still pending"}},
        ]
        call_log: list[str] = []

        async def fake_run_prompt_seed(**kwargs):
            seed_id = str(kwargs["seed"]["seed_id"])
            call_log.append(seed_id)

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": seed_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            out_dir.mkdir()
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            # First run: let both seeds complete.
            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                result = await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    save_dir=str(out_dir),
                    run_id="run-resume",
                )
            self.assertEqual(result["count"], 2)
            self.assertEqual(sorted(call_log), ["seed_000001", "seed_000002"])

            # Second run: seed_000001 and seed_000002 already exist — nothing to do.
            call_log.clear()
            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                result = await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    save_dir=str(out_dir),
                    run_id="run-resume",
                )
            self.assertEqual(call_log, [], "No seeds should have been re-run")
            self.assertEqual(result["count"], 2)

    async def test_run_rollout_discards_transcripts_on_config_change(self) -> None:
        """Changing target model invalidates existing transcripts."""
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "a prompt"}},
        ]

        async def fake_run_prompt_seed(**kwargs):
            seed_id = str(kwargs["seed"]["seed_id"])

            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": seed_id}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(json.dumps(seed_rows[0]) + "\n", encoding="utf-8")

            # First run with model A.
            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/model-a"),
                    save_dir=str(out_dir),
                    run_id="run-cfg",
                )

            # Second run with model B — should discard and re-run.
            with patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed):
                result = await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/model-b"),
                    save_dir=str(out_dir),
                    run_id="run-cfg",
                )
            # count is 1 (re-ran, not 2 = resumed + new)
            self.assertEqual(result["count"], 1)
        seed_rows = [
            {"kind": "prompt", "seed": {"description": "base prompt"}},
            {
                "kind": "scenario",
                "seed_id": "scenario-base",
                "seed": {"title": "Scenario", "description": "scenario description"},
            },
            {
                "kind": "scenario",
                "seed_id": "scenario-second",
                "seed": {"title": "Second scenario", "description": "second description"},
            },
        ]

        async def fake_run_prompt_seed(**kwargs):
            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "prompt", "seed_id": str(kwargs["seed"]["seed_id"])}

            return FakeTranscript()

        async def fake_run_scenario_seed(**kwargs):
            class FakeTranscript:
                def to_dict(self_inner) -> dict[str, str]:
                    return {"kind": "scenario", "seed_id": str(kwargs["seed"]["seed_id"])}

            return FakeTranscript()

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text("\n".join(json.dumps(row) for row in seed_rows) + "\n", encoding="utf-8")

            with (
                patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed),
                patch("p2m.stages.rollout._run_scenario_seed", new=fake_run_scenario_seed),
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(max_turns=1, concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-rollout",
                )

            canonical_rows = load_seeds(seed_path)

        self.assertEqual(
            [row["seed_id"] for row in canonical_rows],
            ["seed_000001", "seed_000002", "seed_000003"],
        )
        self.assertNotIn("parent_seed_id", canonical_rows[2])

    # ------------------------------------------------------------------
    # Per-row refusal isolation (absorbed from PR #44)
    # ------------------------------------------------------------------

    async def test_run_prompt_seed_records_target_input_refusal(self) -> None:
        """Target-side LLMInputError is recorded as a transcript event, not raised.

        Without this, one Azure content-filter refusal kills the entire
        batch even though hundreds of other adversarial prompts completed
        successfully. Absorbed from PR #44 commit 82cf339.
        """
        from p2m.core.session import HostedSession

        async def fake_run_turn(self_inner, messages):
            raise LLMInputError(
                "Bad request: AzureException BadRequestError - Invalid prompt: "
                "your prompt was flagged as potentially violating our usage policy."
            )

        async def fake_open(self_inner):
            return None

        async def fake_close(self_inner):
            return None

        seed_row = {
            "kind": "prompt",
            "seed_id": "seed_000001",
            "seed": {"description": "adversarial prompt the target refuses"},
        }

        with (
            patch.object(HostedSession, "open", new=fake_open),
            patch.object(HostedSession, "close", new=fake_close),
            patch.object(HostedSession, "run_turn", new=fake_run_turn),
        ):
            transcript = await _run_prompt_seed(
                seed=seed_row,
                target=TargetConfig(model="azure/gpt-5.4-mini"),
                rollout=RolloutConfig(max_turns=1, concurrency=1),
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

    async def test_run_rollout_isolates_target_input_refusal_to_one_seed(self) -> None:
        """One target_input_refused seed must not abort the rest of the batch.

        End-to-end check that the target-side refusal handling produces
        a transcript per seed and that the batch returns successfully.
        Absorbed from PR #44 commit 82cf339.
        """
        from p2m.core.session import HostedSession

        seed_rows = [
            {"kind": "prompt", "seed": {"description": f"prompt {i}"}}
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
                    "prompt flagged as potentially violating our usage policy"
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch.object(HostedSession, "open", new=fake_open),
                patch.object(HostedSession, "close", new=fake_close),
                patch.object(HostedSession, "run_turn", new=fake_run_turn),
            ):
                result = await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4-mini"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-refusal-isolation",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(run_turn_calls), 5)
        self.assertEqual(len(transcript_rows), 5)
        self.assertEqual(result["new_count"], 5)

        by_seed = {row["seed_id"]: row for row in transcript_rows}
        refused = by_seed["seed_000003"]
        self.assertEqual(refused["stop_reason"], "target_input_refused")
        refusal_events = [
            event for event in refused["events"]
            if event["edit"]["type"] == "add_message"
            and "[TARGET INPUT REFUSED:" in event["edit"]["message"].get("content", "")
        ]
        self.assertEqual(len(refusal_events), 1)

        for completed_id in ("seed_000001", "seed_000002", "seed_000004", "seed_000005"):
            self.assertEqual(
                by_seed[completed_id]["stop_reason"],
                "completed",
                f"{completed_id} should have completed cleanly",
            )

    async def test_run_rollout_isolates_auditor_input_refusal_to_one_seed(self) -> None:
        """One auditor_input_refused seed must not abort the rest of the batch.

        Absorbed from PR #44 commit f265154. The auditor's adversarial
        system prompt is exactly the kind of input Azure Prompt Shields'
        jailbreak detector flags. The fix routes auditor-side input
        errors into a recorded transcript event with
        stop_reason='auditor_input_refused'.
        """
        seed_rows = [
            {
                "kind": "scenario",
                "seed": {
                    "title": f"Title {i}",
                    "description": f"scenario seed {i}",
                },
            }
            for i in range(5)
        ]
        auditor_calls: list[str] = []

        async def fake_generate(model, messages, options):
            del options
            description_marker = ""
            for msg in messages:
                content = msg.content if hasattr(msg, "content") else msg.get("content", "")
                if "scenario seed" in content:
                    description_marker = content
                    break
            auditor_calls.append(description_marker[:120])
            if "scenario seed 2" in description_marker:
                raise LLMInputError(
                    "Bad request: AzureException BadRequestError - Invalid "
                    "prompt: your prompt was flagged as potentially "
                    "violating our usage policy (Prompt Shields jailbreak)"
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
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch("p2m.stages.rollout.generate", new=fake_generate),
                patch("p2m.stages.rollout._build_target_session", return_value=FakeHostedSession()),
            ):
                result = await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4-mini"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4-mini"),
                        rollout=RolloutConfig(max_turns=1, concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-auditor-refusal",
                )

            transcript_rows = [
                json.loads(line)
                for line in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(auditor_calls), 5)
        self.assertEqual(len(transcript_rows), 5)
        self.assertEqual(result["new_count"], 5)

        by_seed = {row["seed_id"]: row for row in transcript_rows}
        refused = by_seed["seed_000003"]
        self.assertEqual(refused["stop_reason"], "auditor_input_refused")
        refusal_events = [
            event for event in refused["events"]
            if event["edit"]["type"] == "add_message"
            and "[AUDITOR INPUT REFUSED:" in event["edit"]["message"].get("content", "")
        ]
        self.assertEqual(len(refusal_events), 1)

    async def test_run_rollout_still_fails_fast_on_provider_5xx(self) -> None:
        """LLMProviderError (Azure 5xx) is global, not seed-specific.

        Verifies the per-row tolerance didn't accidentally swallow
        auth/rate-limit/5xx errors that should still abort the stage.
        Absorbed from PR #44 commit 82cf339.
        """
        seed_rows = [
            {"kind": "prompt", "seed": {"description": f"prompt {i}"}}
            for i in range(3)
        ]

        async def fake_run_prompt_seed(**kwargs):
            raise LLMProviderError("Azure 503 ServiceUnavailable")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed),
                self.assertRaises(LLMProviderError),
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4-mini"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        auditor=AuditorConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-provider-error",
                )

    async def test_run_rollout_fails_when_untyped_error_ratio_exceeds_threshold(self) -> None:
        """Untyped errors above the failure threshold abort the stage.

        Per-row tolerance is for typed refusals (target_input_refused,
        auditor_input_refused, target_error). When the worker hits
        unrecognised exceptions at scale we should fail loudly because
        that's almost always a systemic problem (config bug, broken
        validation) rather than seed-specific bad luck. Default
        threshold is 10%; this test forces 50% (1/2) and asserts the
        stage raises. The companion test
        ``test_run_rollout_keeps_partial_successful_transcripts_when_later_worker_fails``
        covers the soft-fail path with the threshold relaxed.
        """
        seed_rows = [
            {"kind": "prompt", "seed": {"description": f"prompt {i}"}}
            for i in range(2)
        ]

        async def fake_run_prompt_seed(**kwargs):
            seed_id = str(kwargs["seed"]["seed_id"])
            if seed_id == "seed_000001":
                class FakeTranscript:
                    def to_dict(self_inner) -> dict[str, str]:
                        return {"kind": "prompt", "seed_id": seed_id}

                return FakeTranscript()
            raise RuntimeError("worker exploded")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            seed_path = tmp_path / "seeds.jsonl"
            out_dir = tmp_path / "run"
            seed_path.write_text(
                "\n".join(json.dumps(row) for row in seed_rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {"P2M_ROLLOUT_ERROR_FAIL_RATIO": "0.10"}, clear=False),
                patch("p2m.stages.rollout._run_prompt_seed", new=fake_run_prompt_seed),
                self.assertRaisesRegex(RuntimeError, "worker exploded"),
            ):
                await run_rollout(
                    seed_path=str(seed_path),
                    target=TargetConfig(model="azure/gpt-5.4"),
                    evaluation=EvaluationConfig(
                        judge=JudgeConfig(model="azure/gpt-5.4"),
                        rollout=RolloutConfig(concurrency=1),
                    ),
                    save_dir=str(out_dir),
                    run_id="run-error-ratio",
                )


if __name__ == "__main__":
    unittest.main()
