"""End-to-end tests for the preset library feature.

Covers:
- YAML schema validation for all 32 preset files
- CLI ``library list`` and ``library show`` commands
- Config.py round-trip for every behavior and judge preset
- Override / merge semantics (inline values override preset values)
- Cross-reference integrity (suggested_judge_presets point to real judges)
- Error paths (missing presets, kind mismatches, malformed input)
"""

import json
import unittest
from pathlib import Path

import yaml
from click.testing import CliRunner

from p2m.cli import cli
from p2m.config import load_runtime_context
from p2m.library.loader import (
    KIND_TO_SUBDIR,
    LIBRARY_ROOT,
    VALID_KINDS,
    discover,
    load_preset,
    resolve_preset,
)
from p2m.stages import STAGES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_BEHAVIOR_NAMES = sorted(
    p.stem for p in (LIBRARY_ROOT / "behaviors").glob("*.yaml")
)

ALL_JUDGE_NAMES = sorted(
    p.stem for p in (LIBRARY_ROOT / "judges").glob("*.yaml")
)

BEHAVIOR_REQUIRED_KEYS = {"kind", "name", "version", "tags", "description"}
JUDGE_REQUIRED_KEYS = {"kind", "name", "version", "tags", "description", "dimensions"}


def _base_config(**overrides):
    """Minimal valid config dict with overrides merged in."""
    cfg = {
        "suite": "e2e-test-suite",
        "behavior": {"name": "e2e_test_behavior"},
        "pipeline": {
            "test_set": {"prompt": {"model": {"name": "azure/gpt-5.4"}}},
            "inference": {"target": {"model": {"name": "azure/gpt-5.4"}}},
            "judge": {"model": {"name": "azure/gpt-5.4"}},
        },
    }
    cfg.update(overrides)
    return cfg


def _load_ctx(behavior_dict=None, judge_overrides=None):
    """Helper to build a config and load runtime context."""
    cfg = _base_config()
    if behavior_dict is not None:
        cfg["behavior"] = behavior_dict
    if judge_overrides is not None:
        judge = {"model": {"name": "azure/gpt-5.4"}}
        judge.update(judge_overrides)
        cfg["pipeline"]["judge"] = judge
    return load_runtime_context(cfg, Path("e2e_test.yaml"), stage_modules=STAGES)


# ===================================================================
# 1. YAML schema validation — every file has correct structure & types
# ===================================================================

class BehaviorYamlSchemaTest(unittest.TestCase):
    """Validate every behavior YAML file has the required schema."""

    def test_all_behavior_files_have_required_keys(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                data = load_preset("behavior", name)
                for key in BEHAVIOR_REQUIRED_KEYS:
                    self.assertIn(key, data, f"behavior {name!r} missing key {key!r}")

    def test_behavior_kind_field_is_correct(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                data = load_preset("behavior", name)
                self.assertEqual(data["kind"], "behavior")

    def test_behavior_name_matches_filename(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                data = load_preset("behavior", name)
                self.assertEqual(data["name"], name)

    def test_behavior_version_is_string(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                data = load_preset("behavior", name)
                self.assertIsInstance(data["version"], str)

    def test_behavior_tags_is_list_of_strings(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                data = load_preset("behavior", name)
                self.assertIsInstance(data["tags"], list)
                for tag in data["tags"]:
                    self.assertIsInstance(tag, str, f"tag {tag!r} in {name}")

    def test_behavior_description_is_nonempty_string(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                data = load_preset("behavior", name)
                self.assertIsInstance(data["description"], str)
                self.assertGreater(len(data["description"].strip()), 0)


class JudgeYamlSchemaTest(unittest.TestCase):
    """Validate every judge YAML file has the required schema."""

    def test_all_judge_files_have_required_keys(self):
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                data = load_preset("judge_preset", name)
                for key in JUDGE_REQUIRED_KEYS:
                    self.assertIn(key, data, f"judge {name!r} missing key {key!r}")

    def test_judge_kind_field_is_correct(self):
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                data = load_preset("judge_preset", name)
                self.assertEqual(data["kind"], "judge_preset")

    def test_judge_name_matches_filename(self):
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                data = load_preset("judge_preset", name)
                self.assertEqual(data["name"], name)

    def test_judge_dimensions_is_nonempty_dict(self):
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                data = load_preset("judge_preset", name)
                self.assertIsInstance(data["dimensions"], dict)
                self.assertGreater(len(data["dimensions"]), 0)

    def test_judge_dimensions_have_description_and_rubric(self):
        for name in ALL_JUDGE_NAMES:
            data = load_preset("judge_preset", name)
            for dim_name, dim in data["dimensions"].items():
                with self.subTest(judge=name, dim=dim_name):
                    self.assertIn("description", dim)
                    self.assertIn("rubric", dim)
                    self.assertIsInstance(dim["description"], str)
                    self.assertIsInstance(dim["rubric"], str)
                    self.assertGreater(len(dim["description"].strip()), 0)
                    self.assertGreater(len(dim["rubric"].strip()), 0)

    def test_judge_tags_is_list_of_strings(self):
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                data = load_preset("judge_preset", name)
                self.assertIsInstance(data["tags"], list)
                for tag in data["tags"]:
                    self.assertIsInstance(tag, str)


# ===================================================================
# 2. CLI ``library list`` — table & JSON output, kind filtering, counts
# ===================================================================

class CliLibraryListTest(unittest.TestCase):
    """Test the ``p2m library list`` CLI command end-to-end."""

    def setUp(self):
        self.runner = CliRunner()

    def test_list_all_presets_exit_code(self):
        result = self.runner.invoke(cli, ["library", "list"])
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_list_all_presets_shows_every_name(self):
        result = self.runner.invoke(cli, ["library", "list", "--no-color"])
        for name in ALL_BEHAVIOR_NAMES + ALL_JUDGE_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, result.output)

    def test_list_filter_behavior_only(self):
        result = self.runner.invoke(cli, ["library", "list", "--kind", "behavior"])
        self.assertEqual(result.exit_code, 0)
        # Table should contain no judge_preset kind rows
        self.assertNotIn("judge_preset", result.output)
        # Should contain at least some behavior names
        self.assertIn("travel_planner", result.output)

    def test_list_filter_judge_only(self):
        result = self.runner.invoke(cli, ["library", "list", "--kind", "judge_preset"])
        self.assertEqual(result.exit_code, 0)
        # Table should contain no behavior kind rows
        # (check table output does not show 'behavior' as a kind value)
        for line in result.output.splitlines():
            stripped = line.strip()
            if stripped.startswith("behavior"):
                self.fail(f"Unexpected behavior row in judge list: {line}")
        # Should contain at least some judge names
        self.assertIn("safety-core", result.output)

    def test_list_json_output_is_valid(self):
        result = self.runner.invoke(cli, ["library", "list", "--json"])
        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.output)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), len(ALL_BEHAVIOR_NAMES) + len(ALL_JUDGE_NAMES))

    def test_list_json_entries_have_required_keys(self):
        result = self.runner.invoke(cli, ["library", "list", "--json"])
        data = json.loads(result.output)
        for entry in data:
            with self.subTest(name=entry.get("name")):
                self.assertIn("kind", entry)
                self.assertIn("name", entry)
                self.assertIn("path", entry)

    def test_list_json_filter_behavior(self):
        result = self.runner.invoke(cli, ["library", "list", "--json", "--kind", "behavior"])
        data = json.loads(result.output)
        self.assertEqual(len(data), len(ALL_BEHAVIOR_NAMES))
        self.assertTrue(all(e["kind"] == "behavior" for e in data))

    def test_list_json_filter_judge(self):
        result = self.runner.invoke(cli, ["library", "list", "--json", "--kind", "judge_preset"])
        data = json.loads(result.output)
        self.assertEqual(len(data), len(ALL_JUDGE_NAMES))
        self.assertTrue(all(e["kind"] == "judge_preset" for e in data))


# ===================================================================
# 3. CLI ``library show`` — detail view, auto-detect kind, JSON output
# ===================================================================

class CliLibraryShowTest(unittest.TestCase):
    """Test the ``p2m library show`` CLI command end-to-end."""

    def setUp(self):
        self.runner = CliRunner()

    def test_show_behavior_by_name(self):
        result = self.runner.invoke(cli, ["library", "show", "travel_planner"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("travel_planner", result.output)
        self.assertIn("kind: behavior", result.output)

    def test_show_judge_by_name(self):
        result = self.runner.invoke(cli, ["library", "show", "safety-core"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("safety-core", result.output)
        self.assertIn("kind: judge_preset", result.output)

    def test_show_with_explicit_kind_behavior(self):
        result = self.runner.invoke(
            cli, ["library", "show", "travel_planner", "--kind", "behavior"]
        )
        self.assertEqual(result.exit_code, 0)

    def test_show_with_explicit_kind_judge(self):
        result = self.runner.invoke(
            cli, ["library", "show", "safety-core", "--kind", "judge_preset"]
        )
        self.assertEqual(result.exit_code, 0)

    def test_show_wrong_kind_fails(self):
        # travel_planner is a behavior, not a judge_preset
        result = self.runner.invoke(
            cli, ["library", "show", "travel_planner", "--kind", "judge_preset"]
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_show_nonexistent_preset_fails(self):
        result = self.runner.invoke(cli, ["library", "show", "no_such_preset"])
        self.assertNotEqual(result.exit_code, 0)

    def test_show_json_output_behavior(self):
        result = self.runner.invoke(cli, ["library", "show", "travel_planner", "--json"])
        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.output)
        self.assertEqual(data["kind"], "behavior")
        self.assertEqual(data["name"], "travel_planner")
        self.assertIn("description", data)

    def test_show_json_output_judge(self):
        result = self.runner.invoke(cli, ["library", "show", "safety-core", "--json"])
        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.output)
        self.assertEqual(data["kind"], "judge_preset")
        self.assertEqual(data["name"], "safety-core")
        self.assertIn("dimensions", data)

    def test_show_every_behavior_succeeds(self):
        """Every behavior preset can be shown without error."""
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                result = self.runner.invoke(cli, ["library", "show", name])
                self.assertEqual(result.exit_code, 0, msg=f"{name}: {result.output}")

    def test_show_every_judge_succeeds(self):
        """Every judge preset can be shown without error."""
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                result = self.runner.invoke(cli, ["library", "show", name])
                self.assertEqual(result.exit_code, 0, msg=f"{name}: {result.output}")


# ===================================================================
# 4. Config round-trip — every behavior preset loads through config.py
# ===================================================================

class ConfigBehaviorPresetRoundTripTest(unittest.TestCase):
    """Every behavior preset loads end-to-end through config.py."""

    def test_every_behavior_preset_loads(self):
        for name in ALL_BEHAVIOR_NAMES:
            with self.subTest(behavior=name):
                ctx = _load_ctx(behavior_dict={"preset": name})
                self.assertEqual(ctx["behavior_name"], name)
                self.assertIsInstance(ctx["behavior"], str)
                self.assertGreater(len(ctx["behavior"]), 0)

    def test_preset_populates_description_from_yaml(self):
        # Verify the description comes from the YAML file, not empty
        preset_data = load_preset("behavior", "travel_planner")
        ctx = _load_ctx(behavior_dict={"preset": "travel_planner"})
        # Config may strip trailing whitespace from YAML block scalars
        self.assertEqual(ctx["behavior"].strip(), preset_data["description"].strip())


# ===================================================================
# 5. Config round-trip — every judge preset loads through config.py
# ===================================================================

class ConfigJudgePresetRoundTripTest(unittest.TestCase):
    """Every judge preset loads end-to-end through config.py."""

    def _judge_dims(self, ctx):
        return ctx["evaluation"].judge.dimensions

    def test_every_judge_preset_loads(self):
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                ctx = _load_ctx(judge_overrides={"preset": name})
                dims = self._judge_dims(ctx)
                self.assertIsInstance(dims, list)
                self.assertGreater(len(dims), 0)

    def test_preset_dimensions_have_name_description_rubric(self):
        """All dimensions from a loaded preset have the three required fields."""
        for name in ALL_JUDGE_NAMES:
            ctx = _load_ctx(judge_overrides={"preset": name})
            for dim in self._judge_dims(ctx):
                with self.subTest(judge=name, dim=dim["name"]):
                    self.assertIn("name", dim)
                    self.assertIn("description", dim)
                    self.assertIn("rubric", dim)

    def test_judge_dim_count_matches_yaml(self):
        """Number of dimensions from config matches the YAML file."""
        for name in ALL_JUDGE_NAMES:
            with self.subTest(judge=name):
                preset_data = load_preset("judge_preset", name)
                ctx = _load_ctx(judge_overrides={"preset": name})
                dims = self._judge_dims(ctx)
                self.assertEqual(len(dims), len(preset_data["dimensions"]))


# ===================================================================
# 6. Override / merge semantics
# ===================================================================

class OverrideSemanticsTest(unittest.TestCase):
    """Inline values override preset values (last-write-wins)."""

    def test_inline_name_overrides_behavior_preset(self):
        ctx = _load_ctx(behavior_dict={"preset": "travel_planner", "name": "custom_name"})
        self.assertEqual(ctx["behavior_name"], "custom_name")

    def test_inline_description_overrides_behavior_preset(self):
        ctx = _load_ctx(
            behavior_dict={"preset": "travel_planner", "description": "Custom description."}
        )
        self.assertEqual(ctx["behavior"], "Custom description.")

    def test_inline_dim_overrides_preset_dim(self):
        ctx = _load_ctx(judge_overrides={
            "preset": "safety-core",
            "dimensions": {
                "policy_violation": {
                    "description": "overridden desc",
                    "rubric": "overridden rubric",
                },
            },
        })
        dims = ctx["evaluation"].judge.dimensions
        pv = next(d for d in dims if d["name"] == "policy_violation")
        self.assertEqual(pv["description"], "overridden desc")
        self.assertEqual(pv["rubric"], "overridden rubric")

    def test_inline_dim_adds_to_preset_dims(self):
        ctx = _load_ctx(judge_overrides={
            "preset": "safety-core",
            "dimensions": {
                "new_custom_dim": {
                    "description": "custom dim desc",
                    "rubric": "custom dim rubric",
                },
            },
        })
        dims = ctx["evaluation"].judge.dimensions
        dim_names = [d["name"] for d in dims]
        # Original preset dims should be present
        self.assertIn("policy_violation", dim_names)
        self.assertIn("overrefusal", dim_names)
        # Plus the new custom dim
        self.assertIn("new_custom_dim", dim_names)

    def test_preset_only_no_inline_dims(self):
        """Using a preset with no inline dimensions gets exactly the preset dims."""
        ctx = _load_ctx(judge_overrides={"preset": "safety-core"})
        dims = ctx["evaluation"].judge.dimensions
        preset = load_preset("judge_preset", "safety-core")
        self.assertEqual(len(dims), len(preset["dimensions"]))

    def test_no_preset_with_inline_dims_only(self):
        """Config with no preset, only inline dimensions, should work."""
        ctx = _load_ctx(judge_overrides={
            "dimensions": {
                "my_dim": {
                    "description": "test",
                    "rubric": "test rubric",
                },
            },
        })
        dims = ctx["evaluation"].judge.dimensions
        self.assertEqual(len(dims), 1)
        self.assertEqual(dims[0]["name"], "my_dim")


# ===================================================================
# 6b. Multi-preset combining — pipeline.judge.preset accepts list
# ===================================================================

class MultiPresetTest(unittest.TestCase):
    """`pipeline.judge.preset` accepts a single name or a list of names."""

    def test_list_with_single_preset_equivalent_to_string(self):
        ctx_str = _load_ctx(judge_overrides={"preset": "safety-core"})
        ctx_list = _load_ctx(judge_overrides={"preset": ["safety-core"]})
        names_str = [d["name"] for d in ctx_str["evaluation"].judge.dimensions]
        names_list = [d["name"] for d in ctx_list["evaluation"].judge.dimensions]
        self.assertEqual(names_str, names_list)

    def test_list_combines_dimensions_from_all_presets(self):
        ctx = _load_ctx(judge_overrides={"preset": ["safety-core", "grounding"]})
        names = [d["name"] for d in ctx["evaluation"].judge.dimensions]
        # safety-core contributes: policy_violation, overrefusal
        # grounding contributes:    hallucination, attribution_error
        self.assertIn("policy_violation", names)
        self.assertIn("overrefusal", names)
        self.assertIn("hallucination", names)
        self.assertIn("attribution_error", names)
        self.assertEqual(len(names), 4)

    def test_list_deduplicates_repeated_preset_name(self):
        ctx = _load_ctx(judge_overrides={"preset": ["safety-core", "safety-core"]})
        single = _load_ctx(judge_overrides={"preset": "safety-core"})
        self.assertEqual(
            [d["name"] for d in ctx["evaluation"].judge.dimensions],
            [d["name"] for d in single["evaluation"].judge.dimensions],
        )

    def test_inline_dim_overrides_dim_from_any_preset_in_list(self):
        ctx = _load_ctx(judge_overrides={
            "preset": ["safety-core", "grounding"],
            "dimensions": {
                "hallucination": {
                    "description": "inline override",
                    "rubric": "inline rubric",
                },
            },
        })
        dims = ctx["evaluation"].judge.dimensions
        hallucination = next(d for d in dims if d["name"] == "hallucination")
        self.assertEqual(hallucination["description"], "inline override")
        self.assertEqual(hallucination["rubric"], "inline rubric")

    def test_empty_list_treated_as_no_preset(self):
        ctx = _load_ctx(judge_overrides={
            "preset": [],
            "dimensions": {
                "only_inline": {"description": "x", "rubric": "y"},
            },
        })
        names = [d["name"] for d in ctx["evaluation"].judge.dimensions]
        self.assertEqual(names, ["only_inline"])

    def test_invalid_preset_type_raises(self):
        with self.assertRaisesRegex(ValueError, "pipeline.judge.preset"):
            _load_ctx(judge_overrides={"preset": 42})

    def test_non_string_list_item_raises(self):
        with self.assertRaisesRegex(ValueError, r"pipeline\.judge\.preset\[1\]"):
            _load_ctx(judge_overrides={"preset": ["safety-core", 7]})

    def test_empty_string_in_list_raises(self):
        with self.assertRaisesRegex(ValueError, r"pipeline\.judge\.preset\[0\]"):
            _load_ctx(judge_overrides={"preset": ["  "]})

    def test_unknown_preset_in_list_raises(self):
        with self.assertRaises(ValueError):
            _load_ctx(judge_overrides={"preset": ["safety-core", "does-not-exist"]})


# ===================================================================
# 7. Cross-reference integrity
# ===================================================================

class CrossReferenceTest(unittest.TestCase):
    """Behavior suggested_judge_presets reference real judges."""

    def test_suggested_judge_presets_exist(self):
        for name in ALL_BEHAVIOR_NAMES:
            data = load_preset("behavior", name)
            suggestions = data.get("suggested_judge_presets", [])
            for judge_name in suggestions:
                with self.subTest(behavior=name, judge=judge_name):
                    # This should not raise
                    path = resolve_preset("judge_preset", judge_name)
                    self.assertTrue(path.is_file())


# ===================================================================
# 8. Error paths
# ===================================================================

class ErrorPathTest(unittest.TestCase):
    """Invalid inputs produce clear errors, not crashes."""

    def test_config_invalid_behavior_preset_raises(self):
        with self.assertRaises(ValueError):
            _load_ctx(behavior_dict={"preset": "nonexistent_xyz"})

    def test_config_invalid_judge_preset_raises(self):
        with self.assertRaises(ValueError):
            _load_ctx(judge_overrides={"preset": "nonexistent_xyz"})

    def test_config_behavior_preset_without_name_derives_name(self):
        """Preset without explicit name should derive name from the preset."""
        ctx = _load_ctx(behavior_dict={"preset": "harmful_medical_advice"})
        self.assertEqual(ctx["behavior_name"], "harmful_medical_advice")

    def test_config_no_behavior_name_or_preset_raises(self):
        with self.assertRaises(ValueError):
            _load_ctx(behavior_dict={"description": "orphan desc"})

    def test_cli_show_nonexistent_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["library", "show", "not_a_real_preset"])
        self.assertNotEqual(result.exit_code, 0)

    def test_loader_kind_mismatch_raises(self):
        """Loading a behavior as a judge_preset should raise ValueError."""
        with self.assertRaises(ValueError):
            load_preset("judge_preset", "travel_planner")

    def test_loader_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            load_preset("invalid_kind", "anything")

    def test_resolve_missing_preset_lists_available(self):
        """Error message for missing preset lists available alternatives."""
        with self.assertRaises(ValueError) as cm:
            resolve_preset("behavior", "nonexistent")
        self.assertIn("Available:", str(cm.exception))

    def test_discover_invalid_kind_raises(self):
        with self.assertRaises(ValueError):
            discover("not_a_kind")


# ===================================================================
# 9. Discover completeness
# ===================================================================

class DiscoverCompletenessTest(unittest.TestCase):
    """discover() returns every YAML file in the library."""

    def test_discover_returns_all_behaviors(self):
        results = discover("behavior")
        found = sorted(r["name"] for r in results)
        self.assertEqual(found, ALL_BEHAVIOR_NAMES)

    def test_discover_returns_all_judges(self):
        results = discover("judge_preset")
        found = sorted(r["name"] for r in results)
        self.assertEqual(found, ALL_JUDGE_NAMES)

    def test_discover_all_count(self):
        results = discover()
        self.assertEqual(len(results), len(ALL_BEHAVIOR_NAMES) + len(ALL_JUDGE_NAMES))


# ===================================================================
# 10. Example config with preset — the real eval_config.yaml
# ===================================================================

class ExampleConfigTest(unittest.TestCase):
    """The example eval_config.yaml that uses presets loads correctly."""

    def test_example_travel_planner_config_loads(self):
        config_path = Path("examples/travel_planner_langgraph/eval_config.yaml")
        if not config_path.is_file():
            self.skipTest("Example config not found")
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        ctx = load_runtime_context(raw, config_path, stage_modules=STAGES)
        # Judge should have dimensions from safety-core plus inline overrides
        dims = ctx["evaluation"].judge.dimensions
        dim_names = [d["name"] for d in dims]
        self.assertIn("policy_violation", dim_names)
        self.assertIn("overrefusal", dim_names)

    def test_example_config_inline_overrides_preset(self):
        config_path = Path("examples/travel_planner_langgraph/eval_config.yaml")
        if not config_path.is_file():
            self.skipTest("Example config not found")
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        ctx = load_runtime_context(raw, config_path, stage_modules=STAGES)
        dims = ctx["evaluation"].judge.dimensions
        pv = next(d for d in dims if d["name"] == "policy_violation")
        # The inline override has custom text, not the preset default
        self.assertIn("quality or safety failure", pv["description"])


if __name__ == "__main__":
    unittest.main()
