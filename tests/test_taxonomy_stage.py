import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from p2m.core.model_client import ModelResponse
from p2m.stages.taxonomy import run_taxonomy


class TaxonomyTaskTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_taxonomy_writes_taxonomy_without_extra_metadata(self) -> None:
        calls: list[tuple[str, object]] = []

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            calls.append((schema_name, messages))
            self.assertEqual(schema_name, "taxonomy")
            return ModelResponse(
                text=json.dumps(
                    {
                        "spec": {"name": "Risk", "definition": "Definition"},
                        "definition_of_terms": [],
                        "failure_modes": [
                            {
                                "name": f"failure_mode-{idx}",
                                "definition": f"definition-{idx}",
                                "examples": [f"example-{idx}"],
                                "permissible": False,
                            }
                            for idx in range(5)
                        ],
                    }
                ),
                parsed={
                    "spec": {"name": "Risk", "definition": "Definition"},
                    "definition_of_terms": [],
                    "failure_modes": [
                        {
                            "name": f"failure_mode-{idx}",
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
            with patch("p2m.stages.taxonomy.generate_structured", new=fake_generate_structured):
                result = await run_taxonomy(
                    spec="Harmful advice",
                    model="azure/gpt-5.4",
                    save_dir=tmp_dir,
                )

            taxonomy_path = Path(result["taxonomy_path"])
            self.assertTrue(taxonomy_path.exists())
            payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
            self.assertNotIn("meta", payload)
            self.assertNotIn("validation_results", result)
            self.assertEqual([schema for schema, _ in calls], ["taxonomy"])

    async def test_run_taxonomy_generates_once(self) -> None:
        calls: list[str] = []

        async def fake_generate_structured(model, messages, *, schema_name, json_schema, options):
            calls.append(schema_name)
            self.assertEqual(schema_name, "taxonomy")
            return ModelResponse(
                text=json.dumps(
                    {
                        "spec": {"name": "Risk", "definition": "Definition"},
                        "definition_of_terms": [],
                        "failure_modes": [
                            {
                                "name": f"failure_mode-{idx}",
                                "definition": f"definition-{idx}",
                                "examples": [f"example-{idx}"],
                                "permissible": False,
                            }
                            for idx in range(5)
                        ],
                    }
                ),
                parsed={
                    "spec": {"name": "Risk", "definition": "Definition"},
                    "definition_of_terms": [],
                    "failure_modes": [
                        {
                            "name": f"failure_mode-{idx}",
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
            with patch("p2m.stages.taxonomy.generate_structured", new=fake_generate_structured):
                result = await run_taxonomy(
                    spec="Harmful advice",
                    model="azure/gpt-5.4",
                    save_dir=tmp_dir,
                )

            self.assertEqual(calls, ["taxonomy"])
            self.assertEqual(result["taxonomy"]["spec"]["name"], "Risk")
if __name__ == "__main__":
    unittest.main()
