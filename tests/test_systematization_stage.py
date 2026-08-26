# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.config_model import ModelConfig
from assert_ai.core.model_client import ModelResponse
from assert_ai.stages.systematization import (
    SystematizationResponse,
    run_systematization,
)

SYSTEMATIZATION_DOCUMENT = {
    "behavior": "harmful advice",
    "scope": "Assistant output that operationally enables harmful activity.",
    "impact_analysis": "The output can reduce the effort required to cause harm.",
    "alternative_systematizations": "A topic-only framing was rejected because it is not behaviorally observable.",
    "references": ["Authoritative safety policy"],
    "stakeholder_lenses": [
        {
            "label": "Safety evaluator",
            "expertise": "Distinguishes actionable assistance from high-level discussion.",
        }
    ],
    "reasoning_summary": "The selected framing separates operational enablement from benign discussion.",
    "concept_spec": {
        "behavior": "harmful advice",
        "patterns": [
            {
                "pattern": "The assistant provides [INSTRUCTION_TYPE] for harmful activity.",
                "pattern_role": "problematic",
                "primary_theory": "Operational harm enablement",
                "related_theory": "Misuse facilitation",
                "key_terms": [
                    {
                        "term": "operational guidance",
                        "definition": "Instructions specific enough to act on.",
                    }
                ],
                "slot_components": [
                    {
                        "component": "INSTRUCTION_TYPE",
                        "nested_slot_components": None,
                        "slot_values": [
                            {
                                "slot_value": "complete_procedure",
                                "definition": "An end-to-end sequence of actions.",
                                "example_phrase": "First do X, then do Y.",
                            }
                        ],
                    }
                ],
            }
        ],
    },
}


class SystematizationStageTest(unittest.IsolatedAsyncioTestCase):
    def test_response_schema_matches_prompt_output_contract(self) -> None:
        """Prevent the customer-observed double encoding.

        The old schema forced the prompt's full JSON document into one string,
        producing responses shaped like ``{"systematization":"{\\"behavior\\"...``.
        """
        schema = SystematizationResponse.model_json_schema()

        self.assertEqual(
            set(schema["properties"]),
            {
                "behavior",
                "scope",
                "impact_analysis",
                "alternative_systematizations",
                "references",
                "stakeholder_lenses",
                "reasoning_summary",
                "concept_spec",
            },
        )
        self.assertNotIn("systematization", schema["properties"])
        self.assertNotIn("summary_items", schema["properties"])

    async def test_run_systematization_writes_expected_artifact(self) -> None:
        call_count = 0

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            nonlocal call_count
            call_count += 1
            self.assertEqual(model, "azure/gpt-5.4")
            self.assertEqual(schema_name, "systematization")
            self.assertIn("concept_spec", json_schema["properties"])
            self.assertNotIn("systematization", json_schema["properties"])
            self.assertTrue(options.web_search)
            self.assertEqual(options.reasoning_effort, "high")
            self.assertIn("## Behavior Label\nharmful advice", prompt)
            self.assertIn("## Background Behavior of Interest\nHarmful advice", prompt)
            self.assertIn("concise summary of key synthesis decisions", prompt)
            self.assertNotIn("verbose details of key synthesis decisions", prompt)
            self.assertIn("provider-safe", prompt)
            self.assertIn("square-bracket placeholders", prompt)
            return ModelResponse(model=model, parsed=SYSTEMATIZATION_DOCUMENT)

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured):
                written_path = await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4", reasoning_effort="high"),
                    mode="research",
                )

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(written_path, out_path)
        self.assertEqual(payload["behavior"], "harmful advice")
        self.assertEqual(payload["systematization"], SYSTEMATIZATION_DOCUMENT)
        self.assertNotIn("summary_items", payload)
        self.assertEqual(payload["meta"]["mode"], "research")
        self.assertEqual(payload["meta"]["model"], "azure/gpt-5.4")
        self.assertEqual(payload["meta"]["reasoning_effort"], "high")
        self.assertEqual(call_count, 1)

    async def test_run_systematization_passes_context_and_web_search_override(self) -> None:
        captured: dict[str, object] = {}

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, schema_name, json_schema
            captured["prompt"] = prompt
            captured["web_search"] = options.web_search
            captured["reasoning_effort"] = options.reasoning_effort
            captured["temperature"] = options.temperature
            return ModelResponse(model="azure/o3", parsed=SYSTEMATIZATION_DOCUMENT)

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Risk body",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/o3", temperature=0.2, reasoning_effort="high"),
                    mode="direct",
                    web_search=False,
                    context="A coding agent with shell access.",
                )

        self.assertIn("# Application Context\nA coding agent with shell access.", str(captured["prompt"]))
        self.assertFalse(bool(captured["web_search"]))
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertIsNone(captured["temperature"])



class SystematizationTruncationDetectionTest(unittest.IsolatedAsyncioTestCase):
    """Issue #131: when the response exhausts the model's output budget,
    the stage must raise a clear truncation-specific error instead of the
    opaque generic JSONDecodeError. Detection must cross-recognise both the
    Chat Completions (`length`) and Responses API (`max_output_tokens`)
    finish-reason variants — the latter was the original repro path."""

    async def test_responses_api_truncation_raises_clear_error(self) -> None:
        """The travel-planner failure mode: web_search routes through the
        Responses API which surfaces truncation as `max_output_tokens`."""
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, prompt, schema_name, json_schema, options
            return ModelResponse(
                model="azure/gpt-5.4",
                text='{"behavior":"harmful advice","scope":"Detect when',
                finish_reason="max_output_tokens",
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with (
                patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "truncated.*max_output_tokens.*max_tokens=8000"),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4", max_tokens=8000),
                )

    async def test_chat_completions_length_truncation_raises_clear_error(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, prompt, schema_name, json_schema, options
            return ModelResponse(
                model="azure/gpt-5.4",
                text='{"behavior":"harmful advice","scope":"partial',
                finish_reason="length",
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with (
                patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "truncated.*length"),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4", max_tokens=10000),
                )

    async def test_content_filter_raises_specific_error_and_diagnostic(self) -> None:
        partial_text = '{"behavior":"hate_speech_generation","scope":"partial'

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del prompt, schema_name, json_schema, options
            return ModelResponse(
                model=model,
                text=partial_text,
                finish_reason="content_filter",
                response_id="chatcmpl-systematization-filtered",
                api_mode="chat_completion",
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            diagnostics_dir = Path(tmp_dir) / "diagnostics"
            with (
                patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(
                    ValueError,
                    "provider content filter.*Full response diagnostic",
                ),
            ):
                await run_systematization(
                    behavior="hate_speech_generation",
                    behavior_text="Hate speech generation",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4", max_tokens=8000),
                    diagnostics_dir=str(diagnostics_dir),
                )

            diagnostic_files = list((diagnostics_dir / "systematization").glob("*.json"))
            self.assertEqual(len(diagnostic_files), 1)
            diagnostic = json.loads(diagnostic_files[0].read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["reason"], "content_filtered")
            self.assertEqual(
                diagnostic["response_metadata"]["finish_reason"],
                "content_filter",
            )

    async def test_non_truncation_parse_failure_keeps_original_error(self) -> None:
        """Prior to issue #131, the parse-failure path raised this exact message.
        That behavior is preserved verbatim for non-truncation parse failures."""
        full_text = "this is not json " * 80

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, prompt, schema_name, json_schema, options
            return ModelResponse(
                model="azure/gpt-5.4",
                text=full_text,
                finish_reason="stop",
                status="completed",
                incomplete_details={"reason": "unknown"},
                response_id="resp-systematization-failure",
                api_mode="responses",
                request_payload={"api_key": "secret", "model": "azure/gpt-5.4"},
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            diagnostics_dir = Path(tmp_dir) / "diagnostics"
            with (
                patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "unparseable output.*Full response diagnostic"),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4", max_tokens=10000),
                    diagnostics_dir=str(diagnostics_dir),
                )

            diagnostic_files = list((diagnostics_dir / "systematization").glob("*.json"))
            self.assertEqual(len(diagnostic_files), 1)
            diagnostic = json.loads(diagnostic_files[0].read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["reason"], "unparseable_output")
            self.assertEqual(diagnostic["llm_call"]["derived"]["content"], full_text)
            self.assertEqual(diagnostic["llm_call"]["request"]["api_key"], "[REDACTED]")
            self.assertEqual(diagnostic["response_metadata"]["response_id"], "resp-systematization-failure")

    async def test_schema_validation_failure_writes_diagnostic(self) -> None:
        invalid_document = dict(SYSTEMATIZATION_DOCUMENT)
        invalid_document.pop("scope")

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del prompt, schema_name, json_schema, options
            return ModelResponse(
                model=model,
                parsed=invalid_document,
                text=json.dumps(invalid_document),
                finish_reason="stop",
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            diagnostics_dir = Path(tmp_dir) / "diagnostics"
            with (
                patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "(?s)scope.*Full response diagnostic"),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                    diagnostics_dir=str(diagnostics_dir),
                )

            diagnostic_files = list((diagnostics_dir / "systematization").glob("*.json"))
            self.assertEqual(len(diagnostic_files), 1)
            diagnostic = json.loads(diagnostic_files[0].read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["reason"], "schema_validation_failed")

    async def test_single_attempt_no_retry(self) -> None:
        """Behavioural guarantee: the systematize stage makes exactly one
        model call. Issue #131 must not introduce retry-driven token spend."""
        call_count = 0

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del prompt, schema_name, json_schema, options
            nonlocal call_count
            call_count += 1
            return ModelResponse(
                model=model,
                text='{"behavior":"harmful advice","scope":"truncated',
                finish_reason="max_output_tokens",
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with (
                patch("assert_ai.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaises(ValueError),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4", max_tokens=8000),
                )

        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
