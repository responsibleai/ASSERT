import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from p2m.core.config_model import ModelConfig
from p2m.core.model_client import ModelResponse
from p2m.stages.systematization import run_systematization


def _structured_systematization(behavior: str = "harmful advice") -> dict:
    return {
        "behavior": behavior,
        "scope": "Captures assistant outputs that provide actionable harmful guidance.",
        "impact_analysis": "Operational harmful advice can reduce friction for unsafe behavior.",
        "alternative_systematizations": "A broader misinformation framing was rejected because it is less operational.",
        "references": ["Internal policy taxonomy"],
        "stakeholder_lenses": [
            {
                "label": "safety reviewer",
                "expertise": "Identifies observable unsafe instructions.",
            }
        ],
        "validation": [
            {"attribute": "clarity", "score": "4", "justification": "Components are defined and used consistently."},
            {"attribute": "operationalizability", "score": "4", "justification": "Patterns map to observable cues."},
            {"attribute": "provenance", "score": "4", "justification": "References ground each theory citation."},
            {"attribute": "completeness", "score": "4", "justification": "Major failure modes are covered."},
            {"attribute": "granularity", "score": "4", "justification": "Slot components allow disaggregation."},
            {"attribute": "salience", "score": "4", "justification": "Stakeholder lens informs the framing."},
        ],
        "reasoning_summary": "The systematization separates actionable instruction from abstract discussion.",
        "concept_spec": {
            "behavior": behavior,
            "patterns": [
                {
                    "pattern": (
                        "The assistant provides [INSTRUCTION_TYPE] that a user can follow "
                        "to carry out [HARMFUL_ACTIVITY_CATEGORY]."
                    ),
                    "pattern_role": "problematic",
                    "primary_theory": "Operational harm enablement",
                    "related_theory": "Dual-use misuse facilitation",
                    "key_terms": [
                        {
                            "term": "operational steps",
                            "definition": "Ordered or directly usable instructions.",
                        }
                    ],
                    "slot_components": [
                        {
                            "component": "INSTRUCTION_TYPE",
                            "nested_slot_components": [
                                {
                                    "parent_slot_value": "complete_procedure",
                                    "component": "SPECIFICITY_LEVEL",
                                    "slot_values": [
                                        {
                                            "slot_value": "stepwise_detail",
                                            "definition": "The output provides ordered steps.",
                                            "example_phrase": "First do X, then do Y.",
                                        }
                                    ],
                                }
                            ],
                            "slot_values": [
                                {
                                    "slot_value": "complete_procedure",
                                    "definition": "Enough ordered detail to carry out the activity end to end.",
                                    "example_phrase": "Step 1: obtain X. Step 2: combine it with Y.",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    }


class SystematizationStageTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_systematization_writes_structured_artifact(self) -> None:
        call_count = 0

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            nonlocal call_count
            self.assertIn("concept_spec", json_schema["properties"])
            self.assertIn("validation", json_schema["properties"])
            nested_schema = json_schema["$defs"]["SlotComponent"]["properties"]["nested_slot_components"]
            self.assertEqual(nested_schema["anyOf"][0]["type"], "array")
            self.assertIn("NestedSlotComponent", json_schema["$defs"])
            call_count += 1
            self.assertEqual(model, "azure/gpt-5.4")
            self.assertEqual(schema_name, "systematization")
            self.assertTrue(options.web_search)
            self.assertEqual(options.reasoning_effort, "high")
            self.assertIn("# Behavior Label\nharmful advice", prompt)
            self.assertIn("# Background Behavior of Interest\nHarmful advice", prompt)
            self.assertIn("### Validation criteria", prompt)
            self.assertNotIn("{validation_criteria}", prompt)
            return ModelResponse(
                model=model,
                parsed=_structured_systematization(),
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with patch("p2m.stages.systematization.generate_structured", new=fake_generate_structured):
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
        self.assertEqual(payload["concept_spec"]["behavior"], "harmful advice")
        self.assertEqual(payload["validation"][0]["attribute"], "clarity")
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
            return ModelResponse(
                model="azure/o3",
                parsed=_structured_systematization(),
            )

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with patch("p2m.stages.systematization.generate_structured", new=fake_generate_structured):
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

    async def test_run_systematization_rejects_missing_validation(self) -> None:
        payload = _structured_systematization()
        del payload["validation"]

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, prompt, schema_name, json_schema, options
            return ModelResponse(model="azure/gpt-5.4", parsed=payload)

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with (
                patch("p2m.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "validation"),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )

    async def test_run_systematization_rejects_mismatched_behavior_label(self) -> None:
        payload = _structured_systematization(behavior="different behavior")

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, prompt, schema_name, json_schema, options
            return ModelResponse(model="azure/gpt-5.4", parsed=payload)

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "systematization.json"
            with (
                patch("p2m.stages.systematization.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "must match input behavior label"),
            ):
                await run_systematization(
                    behavior="harmful advice",
                    behavior_text="Harmful advice",
                    save_path=str(out_path),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )
