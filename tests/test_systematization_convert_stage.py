import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from p2m.core.config_model import ModelConfig
from p2m.core.model_client import ModelResponse
from p2m.stages.systematization_convert import GUIDELINE_PROMPT, run_systematization_to_taxonomy


def _structured_systematization(behavior: str = "Harmful advice") -> dict:
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
                    "pattern": "The model provides [DELIVERY_MODE] about harmful activity.",
                    "pattern_role": "problematic",
                    "primary_theory": "Harm enablement",
                    "related_theory": "Dual-use risk",
                    "key_terms": [
                        {
                            "term": "delivery mode",
                            "definition": "How the instruction is packaged.",
                        }
                    ],
                    "slot_components": [
                        {
                            "component": "DELIVERY_MODE",
                            "nested_slot_components": None,
                            "slot_values": [
                                {
                                    "slot_value": "direct_command",
                                    "definition": "Explicit imperative instruction.",
                                    "example_phrase": "Do X then Y.",
                                },
                                {
                                    "slot_value": "embedded_guidance",
                                    "definition": "Operational content wrapped in explanation.",
                                    "example_phrase": "While discussing risks, note that step 1 is...",
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    }


class SystematizationConvertStageTest(unittest.IsolatedAsyncioTestCase):
    def test_guideline_prompt_preserves_converter_specific_contract(self) -> None:
        self.assertIn("Source-faithful", GUIDELINE_PROMPT)
        self.assertIn("pattern_role", GUIDELINE_PROMPT)
        self.assertIn("A single conversation may trigger multiple behavior_categories.", GUIDELINE_PROMPT)
        self.assertIn("Expand patterns via slot values", GUIDELINE_PROMPT)
        self.assertIn("`behavior.definition` must capture the overall scope", GUIDELINE_PROMPT)
        self.assertIn("4–8 concrete text snippets", GUIDELINE_PROMPT)
        self.assertIn("slot_components", GUIDELINE_PROMPT)

    async def test_run_systematization_to_taxonomy_writes_policy(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            self.assertEqual(schema_name, "taxonomy")
            self.assertIn("# SYSTEMATIZATION\n{", prompt)
            self.assertIn('"concept_spec"', prompt)
            self.assertIn("[DELIVERY_MODE]", prompt)
            self.assertNotIn("# SUMMARY ITEMS", prompt)
            self.assertNotIn('"meta"', prompt)
            self.assertIn("12", prompt)
            return ModelResponse(
                model=model,
                parsed={
                    "behavior": {"definition": "Structured definition"},
                    "definition_of_terms": [
                        {
                            "term": "term-a",
                            "definition": "term definition",
                            "examples": ["example"],
                        }
                    ],
                    "behavior_categories": [
                        {
                            "name": "behavior-a",
                            "definition": "behavior definition",
                            "examples": ["example-a"],
                            "permissible": False,
                        }
                    ],
                },
            )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            systematization_path = tmp_path / "systematization.json"
            systematization_path.write_text(
                json.dumps({**_structured_systematization(), "meta": {"source": "ignored"}}),
                encoding="utf-8",
            )

            with patch("p2m.stages.systematization_convert.generate_structured", new=fake_generate_structured):
                result_path = await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                    behavior_category_count_hint=12,
                )

            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["behavior"]["name"], "Harmful advice")
            self.assertEqual(payload["behavior"]["definition"], "Structured definition")
            self.assertEqual(payload["behavior_categories"][0]["name"], "behavior-a")
            self.assertEqual(payload["definition_of_terms"][0]["term"], "term-a")
            self.assertEqual(payload["meta"]["source"], "systematization")
            self.assertEqual(payload["meta"]["systematization_path"], str(systematization_path))

    async def test_run_systematization_to_taxonomy_raises_on_model_failure(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            raise RuntimeError("boom")

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            systematization_path = tmp_path / "systematization.json"
            systematization_path.write_text(json.dumps(_structured_systematization()), encoding="utf-8")

            with (
                patch("p2m.stages.systematization_convert.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(RuntimeError, "boom"),
            ):
                await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )

    async def test_run_systematization_to_taxonomy_rejects_non_boolean_permissible(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            return ModelResponse(
                model=model,
                parsed={
                    "behavior": {"definition": "Structured definition"},
                    "definition_of_terms": [],
                    "behavior_categories": [
                        {
                            "name": "behavior-a",
                            "definition": "behavior definition",
                            "examples": ["example-a"],
                            "permissible": "false",
                        }
                    ],
                },
            )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            systematization_path = tmp_path / "systematization.json"
            systematization_path.write_text(json.dumps(_structured_systematization()), encoding="utf-8")

            with (
                patch("p2m.stages.systematization_convert.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "behavior_categories.permissible"),
            ):
                await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )

    async def test_run_systematization_to_taxonomy_rejects_missing_behavior_name(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            return ModelResponse(
                model=model,
                parsed={
                    "behavior": {"definition": "Structured definition"},
                    "definition_of_terms": [],
                    "behavior_categories": [
                        {
                            "definition": "behavior definition",
                            "examples": ["example-a"],
                            "permissible": False,
                        }
                    ],
                },
            )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            systematization_path = tmp_path / "systematization.json"
            systematization_path.write_text(json.dumps(_structured_systematization()), encoding="utf-8")

            with (
                patch("p2m.stages.systematization_convert.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "behavior_categories.name"),
            ):
                await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )

    async def test_run_systematization_to_taxonomy_rejects_missing_systematization_field(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            systematization_path = tmp_path / "systematization.json"
            payload = _structured_systematization()
            del payload["validation"]
            systematization_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "validation"):
                await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )
