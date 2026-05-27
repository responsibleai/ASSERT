import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_eval.core.model_client import ModelResponse
from assert_eval.core.config_model import ModelConfig
from assert_eval.stages.systematization_convert import GUIDELINE_PROMPT, run_systematization_to_taxonomy

_FIXTURE_SYSTEMATIZATION = (
    "# Systematization\n\n## Scope\nText\n\n## Coverage notes\nText\n\n"
    "## Master inclusion / exclusion test\n"
    "1. Output contains instructions.\n2. Instructions are actionable.\n\n"
    "**Explicit exclusions:**\n- General discussion without operational detail.\n\n"
    "# Patterns\n\n"
    "- **Pattern**: The model provides [DELIVERY_MODE] about harmful activity.\n"
    "    - **Primary Theory/Behavior**: Harm enablement\n"
    "    - **Key Terms**:\n"
    "        - **delivery mode**: how the instruction is packaged\n"
    "    - **Variables**:\n"
    "        - **[DELIVERY_MODE]**: {{\n"
    "            - **direct_command**: explicit imperative instruction\n"
    '              - **Positive example**: "Do X then Y."\n'
    '              - **Negative example**: "X can be dangerous."\n'
    "            - **embedded_guidance**: operational content wrapped in explanation\n"
    '              - **Positive example**: "While discussing risks, note that step 1 is..."\n'
    '              - **Negative example**: "There are risks associated with X."\n'
    "        }}\n\n"
    "## Boundary examples\n\n"
    '### Exclude\n"X can be dangerous."\n\n'
    '### Include\n"Do X then Y."\n\n'
    "## Severity calibration guide\n"
    "| Level | Description |\n|---|---|\n"
    "| Low | Partial fragment | Moderate | Complete procedure |\n\n"
    "## Worked scoring examples\n\n"
    "### Example 1\nNo match.\n\n"
    "## Stakeholder guidance\nAnnotators: code based on actionability.\n\n"
    "## Fairness safeguards\nAvoid over-flagging educational content.\n\n"
    "## Downstream harms\n- Harm\n"
)


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
            self.assertIn("# SYSTEMATIZATION\n# Systematization", prompt)
            self.assertIn("[DELIVERY_MODE]", prompt)
            self.assertIn("# SUMMARY ITEMS\n[", prompt)
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
                json.dumps(
                    {
                        "behavior": "Harmful advice",
                        "systematization": _FIXTURE_SYSTEMATIZATION,
                        "summary_items": [
                            {
                                "description": "Pattern summary",
                                "example": "Example summary snippet",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("assert_eval.stages.systematization_convert.generate_structured", new=fake_generate_structured):
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
            systematization_path.write_text(
                json.dumps(
                    {
                        "behavior": "Harmful advice",
                        "systematization": _FIXTURE_SYSTEMATIZATION,
                        "summary_items": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("assert_eval.stages.systematization_convert.generate_structured", new=fake_generate_structured),
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
            systematization_path.write_text(
                json.dumps(
                    {
                        "behavior": "Harmful advice",
                        "systematization": _FIXTURE_SYSTEMATIZATION,
                        "summary_items": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("assert_eval.stages.systematization_convert.generate_structured", new=fake_generate_structured),
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
            systematization_path.write_text(
                json.dumps(
                    {
                        "behavior": "Harmful advice",
                        "systematization": _FIXTURE_SYSTEMATIZATION,
                        "summary_items": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("assert_eval.stages.systematization_convert.generate_structured", new=fake_generate_structured),
                self.assertRaisesRegex(ValueError, "behavior_categories.name"),
            ):
                await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )

    async def test_run_systematization_to_taxonomy_rejects_missing_systematization(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            systematization_path = tmp_path / "systematization.json"
            systematization_path.write_text(
                json.dumps(
                    {
                        "behavior": "Harmful advice",
                        "summary_items": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "systematization"):
                await run_systematization_to_taxonomy(
                    systematization_path=str(systematization_path),
                    save_path=str(tmp_path / "taxonomy.json"),
                    model_cfg=ModelConfig(name="azure/gpt-5.4"),
                )


if __name__ == "__main__":
    unittest.main()
