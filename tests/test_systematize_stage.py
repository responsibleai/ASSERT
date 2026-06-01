# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.model_client import ModelResponse
from assert_ai.stages.systematize import run_systematize


class PolicyTaskTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_systematize_writes_policy_without_extra_metadata(self) -> None:
        calls: list[tuple[str, object]] = []

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            calls.append((schema_name, messages))
            self.assertEqual(schema_name, "taxonomy")
            return ModelResponse(
                text=json.dumps(
                    {
                        "behavior": {"name": "Risk", "definition": "Definition"},
                        "definition_of_terms": [],
                        "behavior_categories": [
                            {
                                "name": f"behavior-{idx}",
                                "definition": f"definition-{idx}",
                                "examples": [f"example-{idx}"],
                                "permissible": False,
                            }
                            for idx in range(5)
                        ],
                    }
                ),
                parsed={
                    "behavior": {"name": "Risk", "definition": "Definition"},
                    "definition_of_terms": [],
                    "behavior_categories": [
                        {
                            "name": f"behavior-{idx}",
                            "definition": f"definition-{idx}",
                            "examples": [f"example-{idx}"],
                            "permissible": False,
                        }
                        for idx in range(5)
                    ],
                },
                model=model,
            )

        with TemporaryDirectory() as tmp_dir:
            with patch("assert_ai.stages.systematize.generate_structured", new=fake_generate_structured):
                result = await run_systematize(
                    behavior="Harmful advice",
                    model="azure/gpt-5.4",
                    save_dir=tmp_dir,
                )

            taxonomy_path = Path(result["taxonomy_path"])
            self.assertTrue(taxonomy_path.exists())
            payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
            self.assertNotIn("meta", payload)
            self.assertNotIn("validation_results", result)
            self.assertEqual([schema for schema, _ in calls], ["taxonomy"])

    async def test_run_systematize_generates_once(self) -> None:
        calls: list[str] = []

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            calls.append(schema_name)
            self.assertEqual(schema_name, "taxonomy")
            return ModelResponse(
                text=json.dumps(
                    {
                        "behavior": {"name": "Risk", "definition": "Definition"},
                        "definition_of_terms": [],
                        "behavior_categories": [
                            {
                                "name": f"behavior-{idx}",
                                "definition": f"definition-{idx}",
                                "examples": [f"example-{idx}"],
                                "permissible": False,
                            }
                            for idx in range(5)
                        ],
                    }
                ),
                parsed={
                    "behavior": {"name": "Risk", "definition": "Definition"},
                    "definition_of_terms": [],
                    "behavior_categories": [
                        {
                            "name": f"behavior-{idx}",
                            "definition": f"definition-{idx}",
                            "examples": [f"example-{idx}"],
                            "permissible": False,
                        }
                        for idx in range(5)
                    ],
                },
                model=model,
            )

        with TemporaryDirectory() as tmp_dir:
            with patch("assert_ai.stages.systematize.generate_structured", new=fake_generate_structured):
                result = await run_systematize(
                    behavior="Harmful advice",
                    model="azure/gpt-5.4",
                    save_dir=tmp_dir,
                )

            self.assertEqual(calls, ["taxonomy"])
            self.assertEqual(result["taxonomy"]["behavior"]["name"], "Risk")
if __name__ == "__main__":
    unittest.main()
