"""Tests for the validation bridge in ``p2m init``."""

from __future__ import annotations

import unittest
from pathlib import Path

from p2m.init._validate import validate_proposed_yaml, validate_raw_config


_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_TRAVEL_CONFIG = _EXAMPLES_DIR / "travel_planner_langgraph" / "eval_config.yaml"


class ValidateProposedYamlTest(unittest.TestCase):
    @unittest.skipUnless(_TRAVEL_CONFIG.exists(), "travel planner example not found")
    def test_travel_planner_golden_config_is_valid(self) -> None:
        yaml_str = _TRAVEL_CONFIG.read_text(encoding="utf-8")
        ok, errors = validate_proposed_yaml(yaml_str)
        self.assertTrue(ok, f"Unexpected errors: {errors}")
        self.assertEqual(errors, [])

    def test_invalid_yaml_syntax(self) -> None:
        ok, errors = validate_proposed_yaml("{{bad yaml")
        self.assertFalse(ok)
        self.assertTrue(any("YAML" in e or "parse" in e.lower() for e in errors))

    def test_missing_suite_key(self) -> None:
        ok, errors = validate_proposed_yaml("behavior:\n  name: test\n  description: x\n")
        self.assertFalse(ok)

    def test_empty_input(self) -> None:
        ok, errors = validate_proposed_yaml("")
        self.assertFalse(ok)


class ValidateRawConfigTest(unittest.TestCase):
    def test_minimal_valid_config(self) -> None:
        data = {
            "suite": "test_suite",
            "behavior": {
                "name": "test_behavior",
                "description": "A test behavior",
            },
            "context": "Some context",
            "pipeline": {
                "systematize": {},
                "test_set": {},
                "inference": {},
                "judge": {},
            },
        }
        ok, errors = validate_raw_config(data)
        self.assertTrue(ok, f"Unexpected errors: {errors}")

    def test_reserved_dimension_name_behavior(self) -> None:
        data = {
            "suite": "test",
            "behavior": {"name": "b", "description": "d"},
            "context": "c",
            "pipeline": {
                "systematize": {},
                "test_set": {
                    "stratify": {
                        "dimensions": [
                            {"name": "behavior", "values": ["a"]},
                        ],
                    },
                },
                "inference": {},
                "judge": {},
            },
        }
        ok, errors = validate_raw_config(data)
        self.assertFalse(ok)
        self.assertTrue(any("behavior" in e.lower() for e in errors))

    def test_invalid_identifier(self) -> None:
        data = {
            "suite": "has spaces bad",
            "behavior": {"name": "b", "description": "d"},
        }
        ok, errors = validate_raw_config(data)
        self.assertFalse(ok)

    def test_unknown_top_level_keys(self) -> None:
        data = {
            "suite": "test",
            "behavior": {"name": "b", "description": "d"},
            "context": "c",
            "bogus_key": "should fail",
        }
        ok, errors = validate_raw_config(data)
        self.assertFalse(ok)
        self.assertTrue(any("bogus_key" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
