# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Characterization tests for the test-case domain modules."""

from __future__ import annotations

import json
import unittest
from typing import Any

from assert_ai.analysis.stratification_metrics import (
    build_supplementary_metrics,
    confusion_matrices,
    cross_axis_nmi,
    effective_dimensionality,
    intended_vs_observed_metrics,
    behavior_agreement,
)
from assert_ai.analysis.test_case_labeling import (
    _label_entry_schema,
    _labels_response_schema,
    _normalize_observed_label_entry,
    build_labeling_prompt,
)
from assert_ai.stages.stratification import (
    _stratification_response_schema,
    render_stratification_catalog,
    render_behavior_categories,
)
from assert_ai.stages.test_set import (
    PROMPT_FIELD_EXAMPLES,
    SCENARIO_FIELD_EXAMPLES,
    TEST_CASE_SCHEMA,
    _template_replacements,
    normalize_generated_test_case,
    render_tuple_spec,
    test_set_response_schema as make_test_set_response_schema,
)

FACTOR_NAMES = ("domain", "user_context")


def _minimal_stratification(include_behavior: bool = True) -> dict[str, list[dict[str, str]]]:
    stratification = {
        "domain": [
            {"name": "Healthcare", "definition": "Clinical settings."},
            {"name": "Education", "definition": "School settings."},
        ],
        "user_context": [
            {"name": "User is a minor", "definition": "The user is under 18."},
            {"name": "No professional access", "definition": "The user has no access to qualified help."},
        ],
    }
    if include_behavior:
        stratification = {
            "behavior": [
                {"name": "Behavior A", "description": "Definition A"},
                {"name": "Behavior B", "description": "Definition B"},
            ],
            **stratification,
        }
    return stratification


def _minimal_policy() -> dict[str, Any]:
    return {
        "behavior": {"name": "test_risk"},
        "behavior_categories": [
            {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            {"name": "Behavior B", "definition": "Definition B", "permissible": False},
        ],
    }


class NormalizeGeneratedSeedTest(unittest.TestCase):
    def test_keeps_title_description_system_prompt(self) -> None:
        result = normalize_generated_test_case(
            {"title": "T", "description": "D", "system_prompt": "S"},
            tool_source="runtime",
            fixed_system_prompt=None,
        )
        self.assertEqual(result, {"title": "T", "description": "D", "system_prompt": "S"})

    def test_raises_on_missing_description(self) -> None:
        with self.assertRaises(ValueError):
            normalize_generated_test_case(
                {"title": "T", "description": ""},
                tool_source="runtime",
                fixed_system_prompt=None,
            )


class RenderStratificationCatalogTest(unittest.TestCase):
    def test_includes_behavior_when_present(self) -> None:
        result = render_stratification_catalog(_minimal_stratification(), include_behavior=True)
        self.assertIn("Behavior A", result)
        self.assertIn("Healthcare", result)

    def test_excludes_behavior_when_requested(self) -> None:
        result = render_stratification_catalog(_minimal_stratification(), include_behavior=False)
        self.assertNotIn("Behavior A", result)
        self.assertIn("Healthcare", result)


class RenderTupleSpecTest(unittest.TestCase):
    def test_renders_factor_values(self) -> None:
        stratification = _minimal_stratification()
        spec = {
            "domain": stratification["domain"][0],
            "user_context": stratification["user_context"][0],
            "behavior": stratification["behavior"][0],
        }
        result = render_tuple_spec(spec, include_behavior=True)
        self.assertIn("Healthcare", result)
        self.assertIn("Behavior A", result)


class RenderPolicyNodesTest(unittest.TestCase):
    def test_renders_permissible_and_non_permissible(self) -> None:
        result = render_behavior_categories(_minimal_policy())
        self.assertIn("Behavior A (PERMISSIBLE)", result)
        self.assertIn("Behavior B (NOT PERMISSIBLE)", result)


class StratificationResponseSchemaTest(unittest.TestCase):
    def test_schema_has_configured_factors(self) -> None:
        schema = _stratification_response_schema(3, dimensions=FACTOR_NAMES)
        self.assertEqual(set(schema["required"]), set(FACTOR_NAMES))
        self.assertEqual(schema["properties"]["domain"]["items"]["required"], ["name", "definition"])


class SeedsResponseSchemaTest(unittest.TestCase):
    def test_schema_wraps_test_set_array(self) -> None:
        schema = make_test_set_response_schema()
        self.assertEqual(schema["properties"]["test_set"]["items"], TEST_CASE_SCHEMA)

    def test_schema_omits_min_items_by_default(self) -> None:
        schema = make_test_set_response_schema()
        self.assertNotIn("minItems", schema["properties"]["test_set"])
        self.assertEqual(schema["properties"]["test_set"]["maxItems"], 2000)

    def test_schema_pins_min_items_when_count_supplied(self) -> None:
        schema = make_test_set_response_schema(min_items=27)
        self.assertEqual(schema["properties"]["test_set"]["minItems"], 27)
        self.assertEqual(schema["properties"]["test_set"]["maxItems"], 2000)

    def test_schema_ignores_non_positive_min_items(self) -> None:
        for value in (0, -1):
            schema = make_test_set_response_schema(min_items=value)
            self.assertNotIn("minItems", schema["properties"]["test_set"])

    def test_schema_pins_both_bounds_when_min_and_max_match(self) -> None:
        schema = make_test_set_response_schema(min_items=500, max_items=500)
        self.assertEqual(schema["properties"]["test_set"]["minItems"], 500)
        self.assertEqual(schema["properties"]["test_set"]["maxItems"], 500)

    def test_schema_ignores_non_positive_max_items(self) -> None:
        for value in (0, -1):
            schema = make_test_set_response_schema(max_items=value)
            self.assertEqual(schema["properties"]["test_set"]["maxItems"], 2000)


class LabelEntrySchemaTest(unittest.TestCase):
    def test_schema_enumerates_present_factors_only(self) -> None:
        schema = _label_entry_schema(_minimal_stratification(include_behavior=False))
        self.assertEqual(set(schema["required"]), set(FACTOR_NAMES))
        self.assertEqual(
            schema["properties"]["domain"]["enum"],
            ["Healthcare", "Education"],
        )

    def test_schema_includes_behavior_when_present(self) -> None:
        schema = _label_entry_schema(_minimal_stratification())
        self.assertIn("behavior", schema["required"])


class LabelsResponseSchemaTest(unittest.TestCase):
    def test_schema_wraps_label_array_with_count(self) -> None:
        schema = _labels_response_schema(_minimal_stratification(), 5)
        self.assertEqual(schema["properties"]["labels"]["minItems"], 5)
        self.assertEqual(schema["properties"]["labels"]["maxItems"], 5)


class SchemaExampleTest(unittest.TestCase):
    def test_prompt_example_contains_field_examples(self) -> None:
        seed = {
            key: PROMPT_FIELD_EXAMPLES[key]
            for key in TEST_CASE_SCHEMA["properties"]
            if key in PROMPT_FIELD_EXAMPLES
        }
        parsed = json.loads(json.dumps({"test_set": [seed]}, indent=2))
        self.assertEqual(parsed["test_set"][0]["title"], PROMPT_FIELD_EXAMPLES["title"])

    def test_scenario_example_contains_field_examples(self) -> None:
        seed = {
            key: SCENARIO_FIELD_EXAMPLES[key]
            for key in TEST_CASE_SCHEMA["properties"]
            if key in SCENARIO_FIELD_EXAMPLES
        }
        parsed = json.loads(json.dumps({"test_set": [seed]}, indent=2))
        self.assertEqual(parsed["test_set"][0]["title"], SCENARIO_FIELD_EXAMPLES["title"])


class TemplateReplacementsTest(unittest.TestCase):
    def test_taxonomy_body_is_passed_through(self) -> None:
        replacements = _template_replacements(
            "prompt",
            "behavior",
            "beh",
            "def",
            ["ex1"],
            5,
            context=None,
            batch_guidance="",
            taxonomy_body="TAXONOMY-BODY-SENTINEL",
        )
        self.assertEqual(replacements["taxonomy_body"], "TAXONOMY-BODY-SENTINEL")
        self.assertEqual(replacements["behavior"], "beh")
        self.assertEqual(replacements["definition"], "def")
        self.assertIn("ex1", replacements["examples"])

    def test_no_permissible_or_strategy_keys(self) -> None:
        replacements = _template_replacements(
            "scenario",
            "behavior",
            "beh",
            "def",
            [],
            3,
            context=None,
            batch_guidance="",
            taxonomy_body="",
        )
        self.assertNotIn("permissible_status", replacements)
        self.assertNotIn("test_case_strategy", replacements)


class NormalizeObservedLabelEntryTest(unittest.TestCase):
    def test_accepts_valid_entry(self) -> None:
        stratification = _minimal_stratification()
        entry = {
            "behavior": "Behavior A",
            "domain": "Healthcare",
            "user_context": "User is a minor",
        }
        self.assertEqual(_normalize_observed_label_entry(entry, stratification), entry)

    def test_rejects_invalid_name(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_observed_label_entry(
                {"domain": "Missing", "user_context": "User is a minor"},
                _minimal_stratification(include_behavior=False),
            )


class BuildLabelingPromptTest(unittest.TestCase):
    def test_includes_concept_and_catalog(self) -> None:
        result = build_labeling_prompt(
            kind="prompt",
            behavior_name="test_risk",
            stratification=_minimal_stratification(),
            rows=[{"test_case_id": "s1", "seed": {"title": "T", "description": "D", "system_prompt": ""}}],
        )
        self.assertIn("test_risk", result)
        self.assertIn("Healthcare", result)
        self.assertIn("Prompt 1:", result)

    def test_omits_behavior_when_stratification_lacks_it(self) -> None:
        result = build_labeling_prompt(
            kind="scenario",
            behavior_name="test_risk",
            stratification=_minimal_stratification(include_behavior=False),
            rows=[{"test_case_id": "s1", "seed": {"title": "T", "description": "D", "system_prompt": ""}}],
        )
        self.assertNotIn("Taxonomy node", result)
        self.assertIn("Scenario 1:", result)


class MetricsTest(unittest.TestCase):
    def test_effective_dimensionality_handles_assignments(self) -> None:
        stratification = _minimal_stratification()
        assignments = [
            {"behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"},
            {"behavior": "Behavior B", "domain": "Education", "user_context": "No professional access"},
        ]
        result = effective_dimensionality(assignments, stratification)
        self.assertGreater(result["n_components_90"], 0)

    def test_cross_axis_nmi_returns_all_pairs(self) -> None:
        stratification = _minimal_stratification()
        assignments = [
            {"behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"},
            {"behavior": "Behavior B", "domain": "Education", "user_context": "No professional access"},
        ] * 5
        result = cross_axis_nmi(assignments, stratification)
        self.assertEqual(len(result), 3)

    def test_intended_vs_observed_metrics_uses_present_factors(self) -> None:
        stratification = _minimal_stratification(include_behavior=False)
        labels = [{"test_case_id": "s1", "domain": "Healthcare", "user_context": "User is a minor"}]
        result = intended_vs_observed_metrics(labels, labels, stratification)
        self.assertEqual(result["exact_tuple_agreement"], 1.0)

    def test_confusion_matrices_match_level_names(self) -> None:
        stratification = _minimal_stratification()
        labels = [{"test_case_id": "s1", "behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"}]
        result = confusion_matrices(labels, labels, stratification)
        self.assertEqual(result["domain"]["Healthcare"]["Healthcare"], 1)

    def test_behavior_agreement_uses_exact_behavior_name(self) -> None:
        observed = [{"test_case_id": "s1", "behavior": "Behavior A"}]
        rows = [{"test_case_id": "s1", "dimensions": {"behavior": "Behavior A"}}]
        self.assertEqual(behavior_agreement(observed, rows), 1.0)

    def test_build_supplementary_metrics_shape(self) -> None:
        stratification = _minimal_stratification()
        observed = [
            {"test_case_id": "s1", "behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"},
            {"test_case_id": "s2", "behavior": "Behavior B", "domain": "Education", "user_context": "No professional access"},
        ]
        rows = [
            {"test_case_id": "s1", "dimensions": {"behavior": "Behavior A"}},
            {"test_case_id": "s2", "dimensions": {"behavior": "Behavior B"}},
        ]
        result = build_supplementary_metrics(
            kind="scenario",
            stratification=stratification,
            rows=rows,
            observed_assignments=observed,
            intended_assignments=None,
        )
        self.assertIn("stratification_quality", result)
        self.assertIn("labeling_quality", result)
        self.assertIn("factor_counts", result["stratification_quality"])


if __name__ == "__main__":
    unittest.main()
