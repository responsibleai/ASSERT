"""Characterization tests for the seed domain modules."""

from __future__ import annotations

import json
import unittest
from typing import Any

from p2m.analysis.design_metrics import (
    build_supplementary_metrics,
    confusion_matrices,
    cross_axis_nmi,
    effective_dimensionality,
    intended_vs_observed_metrics,
    behavior_agreement,
)
from p2m.analysis.seed_labeling import (
    _label_entry_schema,
    _labels_response_schema,
    _normalize_observed_label_entry,
    build_labeling_prompt,
)
from p2m.stages.design import (
    _design_response_schema,
    render_design_catalog,
    render_behaviors,
)
from p2m.stages.seeds import (
    PROMPT_FIELD_EXAMPLES,
    SCENARIO_FIELD_EXAMPLES,
    SEED_SCHEMA,
    _template_replacements,
    normalize_generated_seed,
    render_tuple_spec,
    seeds_response_schema,
)

FACTOR_NAMES = ("domain", "user_context")


def _minimal_design(include_behavior: bool = True) -> dict[str, list[dict[str, str]]]:
    design = {
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
        design = {
            "behavior": [
                {"name": "Behavior A", "description": "Definition A"},
                {"name": "Behavior B", "description": "Definition B"},
            ],
            **design,
        }
    return design


def _minimal_policy() -> dict[str, Any]:
    return {
        "concept": {"name": "test_risk"},
        "behaviors": [
            {"name": "Behavior A", "definition": "Definition A", "permissible": True},
            {"name": "Behavior B", "definition": "Definition B", "permissible": False},
        ],
    }


class NormalizeGeneratedSeedTest(unittest.TestCase):
    def test_keeps_title_description_system_prompt(self) -> None:
        result = normalize_generated_seed(
            {"title": "T", "description": "D", "system_prompt": "S"},
            tool_source="runtime",
            fixed_system_prompt=None,
        )
        self.assertEqual(result, {"title": "T", "description": "D", "system_prompt": "S"})

    def test_raises_on_missing_description(self) -> None:
        with self.assertRaises(ValueError):
            normalize_generated_seed(
                {"title": "T", "description": ""},
                tool_source="runtime",
                fixed_system_prompt=None,
            )


class RenderDesignCatalogTest(unittest.TestCase):
    def test_includes_behavior_when_present(self) -> None:
        result = render_design_catalog(_minimal_design(), include_behavior=True)
        self.assertIn("Behavior A", result)
        self.assertIn("Healthcare", result)

    def test_excludes_behavior_when_requested(self) -> None:
        result = render_design_catalog(_minimal_design(), include_behavior=False)
        self.assertNotIn("Behavior A", result)
        self.assertIn("Healthcare", result)


class RenderTupleSpecTest(unittest.TestCase):
    def test_renders_factor_values(self) -> None:
        design = _minimal_design()
        spec = {
            "domain": design["domain"][0],
            "user_context": design["user_context"][0],
            "behavior": design["behavior"][0],
        }
        result = render_tuple_spec(spec, include_behavior=True)
        self.assertIn("Healthcare", result)
        self.assertIn("Behavior A", result)


class RenderPolicyNodesTest(unittest.TestCase):
    def test_renders_permissible_and_non_permissible(self) -> None:
        result = render_behaviors(_minimal_policy())
        self.assertIn("Behavior A (PERMISSIBLE)", result)
        self.assertIn("Behavior B (NOT PERMISSIBLE)", result)


class DesignResponseSchemaTest(unittest.TestCase):
    def test_schema_has_configured_factors(self) -> None:
        schema = _design_response_schema(3, factors=FACTOR_NAMES)
        self.assertEqual(set(schema["required"]), set(FACTOR_NAMES))
        self.assertEqual(schema["properties"]["domain"]["items"]["required"], ["name", "definition"])


class SeedsResponseSchemaTest(unittest.TestCase):
    def test_schema_wraps_seeds_array(self) -> None:
        schema = seeds_response_schema()
        self.assertEqual(schema["properties"]["seeds"]["items"], SEED_SCHEMA)

    def test_schema_omits_min_items_by_default(self) -> None:
        schema = seeds_response_schema()
        self.assertNotIn("minItems", schema["properties"]["seeds"])
        self.assertEqual(schema["properties"]["seeds"]["maxItems"], 2000)

    def test_schema_pins_min_items_when_count_supplied(self) -> None:
        schema = seeds_response_schema(min_items=27)
        self.assertEqual(schema["properties"]["seeds"]["minItems"], 27)
        self.assertEqual(schema["properties"]["seeds"]["maxItems"], 2000)

    def test_schema_ignores_non_positive_min_items(self) -> None:
        for value in (0, -1):
            schema = seeds_response_schema(min_items=value)
            self.assertNotIn("minItems", schema["properties"]["seeds"])

    def test_schema_pins_both_bounds_when_min_and_max_match(self) -> None:
        schema = seeds_response_schema(min_items=500, max_items=500)
        self.assertEqual(schema["properties"]["seeds"]["minItems"], 500)
        self.assertEqual(schema["properties"]["seeds"]["maxItems"], 500)

    def test_schema_ignores_non_positive_max_items(self) -> None:
        for value in (0, -1):
            schema = seeds_response_schema(max_items=value)
            self.assertEqual(schema["properties"]["seeds"]["maxItems"], 2000)


class LabelEntrySchemaTest(unittest.TestCase):
    def test_schema_enumerates_present_factors_only(self) -> None:
        schema = _label_entry_schema(_minimal_design(include_behavior=False))
        self.assertEqual(set(schema["required"]), set(FACTOR_NAMES))
        self.assertEqual(
            schema["properties"]["domain"]["enum"],
            ["Healthcare", "Education"],
        )

    def test_schema_includes_behavior_when_present(self) -> None:
        schema = _label_entry_schema(_minimal_design())
        self.assertIn("behavior", schema["required"])


class LabelsResponseSchemaTest(unittest.TestCase):
    def test_schema_wraps_label_array_with_count(self) -> None:
        schema = _labels_response_schema(_minimal_design(), 5)
        self.assertEqual(schema["properties"]["labels"]["minItems"], 5)
        self.assertEqual(schema["properties"]["labels"]["maxItems"], 5)


class SchemaExampleTest(unittest.TestCase):
    def test_prompt_example_contains_field_examples(self) -> None:
        seed = {
            key: PROMPT_FIELD_EXAMPLES[key]
            for key in SEED_SCHEMA["properties"]
            if key in PROMPT_FIELD_EXAMPLES
        }
        parsed = json.loads(json.dumps({"seeds": [seed]}, indent=2))
        self.assertEqual(parsed["seeds"][0]["title"], PROMPT_FIELD_EXAMPLES["title"])

    def test_scenario_example_contains_field_examples(self) -> None:
        seed = {
            key: SCENARIO_FIELD_EXAMPLES[key]
            for key in SEED_SCHEMA["properties"]
            if key in SCENARIO_FIELD_EXAMPLES
        }
        parsed = json.loads(json.dumps({"seeds": [seed]}, indent=2))
        self.assertEqual(parsed["seeds"][0]["title"], SCENARIO_FIELD_EXAMPLES["title"])


class TemplateReplacementsTest(unittest.TestCase):
    def test_policy_body_is_passed_through(self) -> None:
        replacements = _template_replacements(
            "prompt",
            "concept",
            "beh",
            "def",
            ["ex1"],
            5,
            context=None,
            batch_guidance="",
            policy_body="POLICY-BODY-SENTINEL",
        )
        self.assertEqual(replacements["policy_body"], "POLICY-BODY-SENTINEL")
        self.assertEqual(replacements["behavior"], "beh")
        self.assertEqual(replacements["definition"], "def")
        self.assertIn("ex1", replacements["examples"])

    def test_no_permissible_or_strategy_keys(self) -> None:
        replacements = _template_replacements(
            "scenario",
            "concept",
            "beh",
            "def",
            [],
            3,
            context=None,
            batch_guidance="",
            policy_body="",
        )
        self.assertNotIn("permissible_status", replacements)
        self.assertNotIn("seed_strategy", replacements)


class NormalizeObservedLabelEntryTest(unittest.TestCase):
    def test_accepts_valid_entry(self) -> None:
        design = _minimal_design()
        entry = {
            "behavior": "Behavior A",
            "domain": "Healthcare",
            "user_context": "User is a minor",
        }
        self.assertEqual(_normalize_observed_label_entry(entry, design), entry)

    def test_rejects_invalid_name(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_observed_label_entry(
                {"domain": "Missing", "user_context": "User is a minor"},
                _minimal_design(include_behavior=False),
            )


class BuildLabelingPromptTest(unittest.TestCase):
    def test_includes_concept_and_catalog(self) -> None:
        result = build_labeling_prompt(
            kind="prompt",
            concept_name="test_risk",
            design=_minimal_design(),
            rows=[{"seed_id": "s1", "seed": {"title": "T", "description": "D", "system_prompt": ""}}],
        )
        self.assertIn("test_risk", result)
        self.assertIn("Healthcare", result)
        self.assertIn("Prompt 1:", result)

    def test_omits_behavior_when_design_lacks_it(self) -> None:
        result = build_labeling_prompt(
            kind="scenario",
            concept_name="test_risk",
            design=_minimal_design(include_behavior=False),
            rows=[{"seed_id": "s1", "seed": {"title": "T", "description": "D", "system_prompt": ""}}],
        )
        self.assertNotIn("Policy node", result)
        self.assertIn("Scenario 1:", result)


class MetricsTest(unittest.TestCase):
    def test_effective_dimensionality_handles_assignments(self) -> None:
        design = _minimal_design()
        assignments = [
            {"behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"},
            {"behavior": "Behavior B", "domain": "Education", "user_context": "No professional access"},
        ]
        result = effective_dimensionality(assignments, design)
        self.assertGreater(result["n_components_90"], 0)

    def test_cross_axis_nmi_returns_all_pairs(self) -> None:
        design = _minimal_design()
        assignments = [
            {"behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"},
            {"behavior": "Behavior B", "domain": "Education", "user_context": "No professional access"},
        ] * 5
        result = cross_axis_nmi(assignments, design)
        self.assertEqual(len(result), 3)

    def test_intended_vs_observed_metrics_uses_present_factors(self) -> None:
        design = _minimal_design(include_behavior=False)
        labels = [{"seed_id": "s1", "domain": "Healthcare", "user_context": "User is a minor"}]
        result = intended_vs_observed_metrics(labels, labels, design)
        self.assertEqual(result["exact_tuple_agreement"], 1.0)

    def test_confusion_matrices_match_level_names(self) -> None:
        design = _minimal_design()
        labels = [{"seed_id": "s1", "behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"}]
        result = confusion_matrices(labels, labels, design)
        self.assertEqual(result["domain"]["Healthcare"]["Healthcare"], 1)

    def test_behavior_agreement_uses_exact_behavior_name(self) -> None:
        observed = [{"seed_id": "s1", "behavior": "Behavior A"}]
        rows = [{"seed_id": "s1", "factors": {"behavior": "Behavior A"}}]
        self.assertEqual(behavior_agreement(observed, rows), 1.0)

    def test_build_supplementary_metrics_shape(self) -> None:
        design = _minimal_design()
        observed = [
            {"seed_id": "s1", "behavior": "Behavior A", "domain": "Healthcare", "user_context": "User is a minor"},
            {"seed_id": "s2", "behavior": "Behavior B", "domain": "Education", "user_context": "No professional access"},
        ]
        rows = [
            {"seed_id": "s1", "factors": {"behavior": "Behavior A"}},
            {"seed_id": "s2", "factors": {"behavior": "Behavior B"}},
        ]
        result = build_supplementary_metrics(
            kind="scenario",
            design=design,
            rows=rows,
            observed_assignments=observed,
            intended_assignments=None,
        )
        self.assertIn("design_quality", result)
        self.assertIn("labeling_quality", result)
        self.assertIn("factor_counts", result["design_quality"])


if __name__ == "__main__":
    unittest.main()
