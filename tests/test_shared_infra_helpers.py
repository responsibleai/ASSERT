import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from p2m.core.io import (
    load_seeds,
    resolve_path,
    write_jsonl,
)
from p2m.core.judge import (
    BUILT_IN_DIMENSIONS,
    build_judge_contract,
    multi_judge,
    run_judge,
    run_transcript_judge,
)
from p2m.core.model_client import ModelResponse
from p2m.core.transcript import AddMessageEdit, Message, Transcript, TranscriptEvent, TranscriptMetadata
from p2m.core.tools import build_target_tools


class SharedInfraHelpersTest(unittest.IsolatedAsyncioTestCase):
    def _transcript_with_assistant_message(self, content: str) -> tuple[Transcript, dict[str, str]]:
        transcript = Transcript(
            metadata=TranscriptMetadata(
                kind="scenario",
                seed_id="seed-1",
                concept="concept",
                target="target",
                factors={"behavior": "behavior"},
                auditor_model="auditor",
            )
        )
        transcript.add_event(
            TranscriptEvent(
                view=["target", "combined"],
                actor="target",
                edit=AddMessageEdit(message=Message(role="assistant", content=content)),
            )
        )
        _, index_to_message_id = transcript.format_transcript_xml(
            "target",
            skip_system=False,
        )
        return transcript, index_to_message_id

    def test_build_target_tools_returns_openai_tool_dicts(self) -> None:
        tools = build_target_tools(
            [
                {
                    "name": "lookup_record",
                    "description": "Look up a record.",
                    "parameters": [
                        {"name": "record_id", "type": "string", "description": "Record ID"},
                        {"name": "fields", "type": "array", "description": "Fields to fetch"},
                    ],
                }
            ]
        )

        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "lookup_record")
        self.assertEqual(tool["function"]["parameters"]["required"], ["record_id", "fields"])
        self.assertEqual(
            tool["function"]["parameters"]["properties"]["fields"]["items"],
            {"type": "string"},
        )

    def test_utils_shim_write_jsonl_and_load_seeds_round_trip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "seeds.jsonl"
            write_jsonl(path, [{"prompt": "one"}, {"prompt": "two"}])

            rows = load_seeds(path)
            self.assertEqual(rows, [{"prompt": "one"}, {"prompt": "two"}])

    def test_utils_shim_load_seeds_skips_malformed_lines_when_not_strict(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "seeds.jsonl"
            path.write_text('{"prompt":"ok"}\nnot-json\n', encoding="utf-8")

            rows = load_seeds(path, strict=False)
            self.assertEqual(rows, [{"prompt": "ok"}])

            with self.assertRaises(ValueError):
                load_seeds(path, strict=True)

    def test_built_in_dimensions_include_required_base_metrics(self) -> None:
        self.assertEqual(
            [dim["name"] for dim in BUILT_IN_DIMENSIONS],
            ["policy_violation", "overrefusal"],
        )

    def test_utils_shim_resolve_path_preserves_absolute_paths(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "artifact.json"
            path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            self.assertEqual(resolve_path(path), path)

    async def test_multi_judge_uses_structured_path_when_schema_present(self) -> None:
        class ResponseSchema:
            name = "judge_schema"
            json_schema = {
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "boolean"},
                            "overrefusal": {"type": "boolean"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "dimension_justifications": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "string"},
                            "overrefusal": {"type": "string"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "node_judgments": {"type": "array"},
                },
                "required": ["dimensions", "dimension_justifications", "node_judgments"],
                "additionalProperties": False,
            }

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            self.assertEqual(schema_name, "judge_schema")
            self.assertEqual(messages[0].role, "system")
            self.assertEqual(messages[1].role, "user")
            return ModelResponse(
                parsed={
                    "dimensions": {"policy_violation": False, "overrefusal": False},
                    "dimension_justifications": {
                        "policy_violation": "good [1]",
                        "overrefusal": "good [1]",
                    },
                    "node_judgments": [],
                },
                text='{"dimensions":{"policy_violation":false,"overrefusal":false},"dimension_justifications":{"policy_violation":"good [1]","overrefusal":"good [1]"},"node_judgments":[]}',
                model=model,
            )

        with patch("p2m.core.judge.generate_structured", new=fake_generate_structured):
            result = await multi_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                score_keys=["policy_violation"],
                judge_n=1,
                judge_temperature=0.0,
                judge_max_tokens=200,
                response_schema=ResponseSchema(),
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"]["dimensions"]["policy_violation"], False)

    async def test_multi_judge_uses_raw_json_when_structured_parsed_dict_is_incomplete(self) -> None:
        class ResponseSchema:
            name = "judge_schema"
            json_schema = {
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "boolean"},
                            "overrefusal": {"type": "boolean"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "dimension_justifications": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "string"},
                            "overrefusal": {"type": "string"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "node_judgments": {"type": "array"},
                },
                "required": ["dimensions", "dimension_justifications", "node_judgments"],
                "additionalProperties": False,
            }

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            self.assertEqual(schema_name, "judge_schema")
            self.assertEqual(messages[0].role, "system")
            self.assertEqual(messages[1].role, "user")
            return ModelResponse(
                parsed={"dimensions": {"policy_violation": False, "overrefusal": False}},
                text='{"dimensions":{"policy_violation":false,"overrefusal":false},"dimension_justifications":{"policy_violation":"good [1]","overrefusal":"good [1]"},"node_judgments":[]}',
                model=model,
            )

        with patch("p2m.core.judge.generate_structured", new=fake_generate_structured):
            result = await multi_judge(
                judge_model="github_copilot/claude-opus-4.6",
                system_prompt="system",
                user_message="user",
                score_keys=["policy_violation"],
                judge_n=1,
                judge_temperature=0.0,
                judge_max_tokens=200,
                response_schema=ResponseSchema(),
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"]["dimensions"]["policy_violation"], False)

    async def test_multi_judge_retries_without_schema_when_structured_output_is_freeform(self) -> None:
        class ResponseSchema:
            name = "judge_schema"
            json_schema = {
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "boolean"},
                            "overrefusal": {"type": "boolean"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "dimension_justifications": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "string"},
                            "overrefusal": {"type": "string"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "node_judgments": {"type": "array"},
                },
                "required": ["dimensions", "dimension_justifications", "node_judgments"],
                "additionalProperties": False,
            }

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            self.assertEqual(schema_name, "judge_schema")
            self.assertEqual(messages[0].role, "system")
            self.assertEqual(messages[1].role, "user")
            return ModelResponse(
                parsed=None,
                text="Let me think through the policy carefully before deciding.",
                model=model,
            )

        async def fake_generate(model, messages, *, options):
            self.assertEqual(messages[0].role, "system")
            self.assertEqual(messages[1].role, "user")
            return ModelResponse(
                text='{"dimensions":{"policy_violation":false,"overrefusal":false},"dimension_justifications":{"policy_violation":"good [1]","overrefusal":"good [1]"},"node_judgments":[]}',
                model=model,
            )

        with (
            patch("p2m.core.judge.generate_structured", new=fake_generate_structured),
            patch("p2m.core.judge.generate", new=fake_generate),
        ):
            result = await multi_judge(
                judge_model="github_copilot/claude-opus-4.6",
                system_prompt="system",
                user_message="user",
                score_keys=["policy_violation"],
                judge_n=1,
                judge_temperature=0.0,
                judge_max_tokens=200,
                response_schema=ResponseSchema(),
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"]["dimensions"]["policy_violation"], False)

    async def test_multi_judge_does_not_retry_without_schema_for_non_copilot_models(self) -> None:
        class ResponseSchema:
            name = "judge_schema"
            json_schema = {
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "boolean"},
                            "overrefusal": {"type": "boolean"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "dimension_justifications": {
                        "type": "object",
                        "properties": {
                            "policy_violation": {"type": "string"},
                            "overrefusal": {"type": "string"},
                        },
                        "required": ["policy_violation", "overrefusal"],
                        "additionalProperties": False,
                    },
                    "node_judgments": {"type": "array"},
                },
                "required": ["dimensions", "dimension_justifications", "node_judgments"],
                "additionalProperties": False,
            }

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            self.assertEqual(model, "azure/gpt-5.4")
            return ModelResponse(
                parsed=None,
                text="Let me think through the policy carefully before deciding.",
                model=model,
            )

        async def fake_generate(model, messages, *, options):
            raise AssertionError("non-copilot models should not retry without schema")

        with (
            patch("p2m.core.judge.generate_structured", new=fake_generate_structured),
            patch("p2m.core.judge.generate", new=fake_generate),
        ):
            result = await multi_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                score_keys=["policy_violation"],
                judge_n=1,
                judge_temperature=0.0,
                judge_max_tokens=200,
                response_schema=ResponseSchema(),
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["failures"], 1)
        self.assertIsNone(result["verdict"])

    def test_build_judge_contract_uses_highlight_citations(self) -> None:
        contract = build_judge_contract(
            template="Judge {{policy_json}} {{dimensions_section}} {{output_schema}}",
            policy_raw={"behaviors": []},
            judge_dimensions=[],
            schema_name="xml_judgment",
        )

        self.assertEqual(contract["response_schema"]["name"], "xml_judgment")
        self.assertIn("policy_violation", contract["score_keys"])
        self.assertIn("overrefusal", contract["score_keys"])
        self.assertIn("dimension_justifications", contract["response_schema"]["json_schema"]["required"])
        self.assertIn("highlights", contract["response_schema"]["json_schema"]["required"])
        self.assertEqual(contract["response_schema"]["json_schema"]["properties"]["highlights"]["type"], "string")
        self.assertNotIn("citations", contract["response_schema"]["json_schema"]["properties"])
        self.assertIn('"behaviors": []', contract["system_prompt"])

    def test_build_judge_contract_uses_behavior_names_enum(self) -> None:
        contract = build_judge_contract(
            template="Judge {{policy_json}} {{dimensions_section}} {{output_schema}}",
            policy_raw={"behaviors": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
            judge_dimensions=[],
            schema_name="xml_judgment",
        )

        node_schema = contract["response_schema"]["json_schema"]["properties"]["node_judgments"]
        self.assertEqual(node_schema["maxItems"], 3)
        self.assertEqual(
            node_schema["items"]["properties"]["node_name"]["enum"],
            ["a", "b", "c"],
        )

    def test_build_judge_contract_rejects_duplicate_behavior_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate name"):
            build_judge_contract(
                template="Judge {{policy_json}} {{dimensions_section}} {{output_schema}}",
                policy_raw={"behaviors": [{"name": "a"}, {"name": "a"}]},
                judge_dimensions=[],
            )

    def test_build_judge_contract_strips_behavior_names_in_enum(self) -> None:
        contract = build_judge_contract(
            template="Judge {{policy_json}} {{dimensions_section}} {{output_schema}}",
            policy_raw={"behaviors": [{"name": "  a  "}, {"name": "b"}]},
            judge_dimensions=[],
        )
        node_schema = contract["response_schema"]["json_schema"]["properties"]["node_judgments"]
        self.assertEqual(
            node_schema["items"]["properties"]["node_name"]["enum"],
            ["a", "b"],
        )

    async def test_run_judge_returns_normalized_success_payload(self) -> None:
        async def fake_multi_judge(**kwargs):
            self.assertEqual(kwargs["judge_model"], "azure/gpt-5.4")
            return {
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": False},
                    "dimension_justifications": {
                        "policy_violation": "good [1]",
                        "overrefusal": "good [1]",
                    },
                    "node_judgments": [],
                },
                "raw": '{"dimensions":{"policy_violation":false,"overrefusal":false},"dimension_justifications":{"policy_violation":"good [1]","overrefusal":"good [1]"},"node_judgments":[]}',
                "multi_judge": {
                    "n": 2,
                    "n_failed": 0,
                    "votes": {"policy_violation": [False, False], "overrefusal": [False, False]},
                },
                "success": True,
                "failures": 0,
            }

        with patch("p2m.core.judge.multi_judge", new=fake_multi_judge):
            result = await run_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                score_keys=["policy_violation"],
                judge_n=2,
                judge_temperature=0.4,
                judge_max_tokens=200,
                response_schema={"name": "judge_schema", "json_schema": {"type": "object"}},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertIsNone(result["judge_error"])
        self.assertEqual(result["verdict"]["dimensions"]["policy_violation"], False)
        self.assertEqual(result["verdict"]["dimension_justifications"]["policy_violation"], "good [1]")
        self.assertEqual(result["score_values"]["policy_violation"], 0.0)
        self.assertEqual(result["score_meta"]["policy_violation_raw"], False)
        self.assertEqual(result["multi_judge"]["n"], 2)
        self.assertEqual(result["multi_judge"]["n_failed"], 0)

    async def test_run_judge_returns_normalized_failure_payload(self) -> None:
        async def fake_multi_judge(**kwargs):
            self.assertEqual(kwargs["score_keys"], ["policy_violation"])
            return {
                "verdict": None,
                "raw": "bad output",
                "multi_judge": None,
                "success": False,
                "failures": 1,
            }

        with patch("p2m.core.judge.multi_judge", new=fake_multi_judge):
            result = await run_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                score_keys=["policy_violation"],
            )

        self.assertEqual(result["judge_status"], "judge_failed")
        self.assertEqual(result["judge_error"], "judge_failed")
        self.assertEqual(result["verdict"], {"error": "judge_failed"})
        self.assertEqual(result["score_values"], {"policy_violation": 0.0})
        self.assertEqual(result["score_meta"], {})
        self.assertIsNone(result["multi_judge"])
        self.assertEqual(result["raw"], "bad output")

    async def test_run_transcript_judge_normalizes_sparse_nodes(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message(
            "Stay calm and call the nurse if this keeps getting worse."
        )

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "Allowed supportive guidance [1]",
                            "overrefusal": "The assistant helped instead of refusing [1]",
                        },
                        "node_judgments": [
                            {
                                "node_name": "b",
                                "violated": False,
                                "confidence": "high",
                                "reasoning": "Allowed supportive guidance [1]",
                            }
                        ],
                        "highlights": '1. <cite id="1" description="Supportive guidance">Stay calm and call the nurse if this keeps getting worse.</cite>',
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": [{"name": "a"}, {"name": "b"}]},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertEqual(result["verdict"]["justification"], "Allowed supportive guidance [1]")
        self.assertEqual(result["verdict"]["node_judgments"][0]["node_name"], "a")
        self.assertFalse(result["verdict"]["node_judgments"][0]["relevant"])
        self.assertEqual(result["verdict"]["node_judgments"][1]["node_name"], "b")
        self.assertTrue(result["verdict"]["node_judgments"][1]["relevant"])
        self.assertEqual(result["verdict"]["node_judgments"][1]["evidence_turns"], [1])
        self.assertIn("citations", result["verdict"])

    async def test_run_transcript_judge_accepts_empty_highlights_with_warning(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message("Hello")

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "safe [1]",
                            "overrefusal": "safe [1]",
                        },
                        "node_judgments": [],
                        "highlights": "",
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": []},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertIsNone(result["judge_error"])
        self.assertIn("missing_citations", result["verdict"].get("citation_warnings", []))

    async def test_run_transcript_judge_returns_hard_failure_for_missing_dimensions(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message("Hello")

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "node_judgments": [],
                        "highlights": "",
                    }
                ],
                ['{"judge":"bad"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": []},
            )

        self.assertEqual(result["judge_status"], "judge_failed")
        self.assertEqual(result["judge_error"], "missing_dimensions")

    async def test_run_transcript_judge_deduplicates_node_name_with_warning(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message(
            "Stay calm and call the nurse if this keeps getting worse."
        )

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "Allowed supportive guidance [1]",
                            "overrefusal": "The assistant helped [1]",
                        },
                        "node_judgments": [
                            {
                                "node_name": "a",
                                "violated": False,
                                "confidence": "high",
                                "reasoning": "First occurrence [1]",
                            },
                            {
                                "node_name": "a",
                                "violated": True,
                                "confidence": "medium",
                                "reasoning": "Duplicate [1]",
                            },
                        ],
                        "highlights": '1. <cite id="1" description="Supportive guidance">Stay calm and call the nurse if this keeps getting worse.</cite>',
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": [{"name": "a"}, {"name": "b"}]},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertIn("duplicate_node_name:a", result["verdict"].get("judgment_warnings", []))
        relevant_nodes = [n for n in result["verdict"]["node_judgments"] if n.get("relevant")]
        self.assertEqual(len(relevant_nodes), 1)
        self.assertFalse(relevant_nodes[0]["violated"])

    async def test_run_transcript_judge_rejects_unknown_node_name(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message(
            "Stay calm and call the nurse if this keeps getting worse."
        )

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "Allowed supportive guidance [1]",
                            "overrefusal": "The assistant helped [1]",
                        },
                        "node_judgments": [
                            {
                                "node_name": "not-a-real-behavior",
                                "violated": False,
                                "confidence": "high",
                                "reasoning": "Allowed supportive guidance [1]",
                            },
                        ],
                        "highlights": '1. <cite id="1" description="Supportive guidance">Stay calm and call the nurse if this keeps getting worse.</cite>',
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": [{"name": "a"}, {"name": "b"}]},
            )

        self.assertEqual(result["judge_status"], "judge_failed")
        self.assertEqual(result["judge_error"], "unknown_node_name")

    async def test_run_transcript_judge_accepts_missing_citation_markers_with_warning(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message(
            "Stay calm and call the nurse if this keeps getting worse."
        )

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "No violation found [1]",
                            "overrefusal": "The assistant did not overrefuse.",
                        },
                        "node_judgments": [
                            {
                                "node_name": "a",
                                "violated": False,
                                "confidence": "high",
                                "reasoning": "Allowed supportive guidance [1]",
                            },
                        ],
                        "highlights": '1. <cite id="1" description="Supportive guidance">Stay calm and call the nurse if this keeps getting worse.</cite>',
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": [{"name": "a"}]},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertIn("missing_inline_citation_marker", result["verdict"].get("judgment_warnings", []))

    async def test_run_transcript_judge_accepts_dangling_citation_marker_with_warning(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message(
            "Stay calm and call the nurse if this keeps getting worse."
        )

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "No violation [1]",
                            "overrefusal": "No overrefusal [2]",
                        },
                        "node_judgments": [],
                        "highlights": '1. <cite id="1" description="Evidence">Stay calm and call the nurse if this keeps getting worse.</cite>',
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": []},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertIn("dangling_inline_citation_marker", result["verdict"].get("judgment_warnings", []))

    async def test_run_transcript_judge_nodes_with_empty_highlights_get_empty_evidence(self) -> None:
        transcript, index_to_message_id = self._transcript_with_assistant_message(
            "Stay calm and call the nurse if this keeps getting worse."
        )

        async def fake_run_judge_attempts(*args, **kwargs):
            return (
                [
                    {
                        "dimensions": {
                            "policy_violation": False,
                            "overrefusal": False,
                        },
                        "dimension_justifications": {
                            "policy_violation": "No violation found",
                            "overrefusal": "No overrefusal",
                        },
                        "node_judgments": [
                            {
                                "node_name": "a",
                                "violated": False,
                                "confidence": "high",
                                "reasoning": "Allowed supportive guidance",
                            },
                        ],
                        "highlights": "",
                    }
                ],
                ['{"judge":"ok"}'],
                0,
            )

        with patch("p2m.core.judge._run_judge_attempts", new=fake_run_judge_attempts):
            result = await run_transcript_judge(
                judge_model="azure/gpt-5.4",
                system_prompt="system",
                user_message="user",
                transcript=transcript,
                index_to_message_id=index_to_message_id,
                score_keys=["policy_violation", "overrefusal"],
                policy_raw={"behaviors": [{"name": "a"}, {"name": "b"}]},
            )

        self.assertEqual(result["judge_status"], "ok")
        self.assertIn("missing_citations", result["verdict"].get("citation_warnings", []))
        relevant = [n for n in result["verdict"]["node_judgments"] if n.get("relevant")]
        self.assertEqual(len(relevant), 1)
        self.assertEqual(relevant[0]["evidence_turns"], [])


if __name__ == "__main__":
    unittest.main()
