# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for library.loader — preset discovery and loading."""

import unittest

from assert_ai.library.loader import (
    VALID_KINDS,
    discover,
    load_preset,
    resolve_preset,
)


class ResolvePresetTest(unittest.TestCase):
    def test_resolve_judge_preset(self) -> None:
        path = resolve_preset("judge_preset", "safety-core")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "safety-core.yaml")

    def test_resolve_behavior(self) -> None:
        path = resolve_preset("behavior", "prompt_injection")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "prompt_injection.yaml")

    def test_resolve_scenario(self) -> None:
        # travel_planner is an application scenario, not an atomic behavior.
        path = resolve_preset("scenario", "travel_planner")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "travel_planner.yaml")
        self.assertEqual(path.parent.name, "scenarios")

    def test_resolve_moved_scenarios_as_behavior_warns(self) -> None:
        # Existing configs use these names as behavior presets. Keep those
        # historical aliases working, but make the reclassification visible.
        # FutureWarning, not DeprecationWarning: the latter is suppressed by
        # default outside pytest/-W.
        for name in (
            "telecom_customer_service",
            "travel_planner",
            "travel_planner_benchmark",
        ):
            with self.subTest(name=name), self.assertWarns(FutureWarning):
                path = resolve_preset("behavior", name)
            self.assertEqual(path.parent.name, "scenarios")

    def test_resolve_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError, msg="Unknown preset kind"):
            resolve_preset("unknown_kind", "anything")

    def test_resolve_missing_name_raises(self) -> None:
        with self.assertRaises(ValueError, msg="not found"):
            resolve_preset("judge_preset", "nonexistent")


class LoadPresetTest(unittest.TestCase):
    def test_load_judge_preset(self) -> None:
        data = load_preset("judge_preset", "safety-core")
        self.assertEqual(data["kind"], "judge_preset")
        self.assertEqual(data["name"], "safety-core")
        self.assertIn("dimensions", data)
        self.assertIsInstance(data["dimensions"], dict)

    def test_load_behavior(self) -> None:
        data = load_preset("behavior", "prompt_injection")
        self.assertEqual(data["kind"], "behavior")
        self.assertEqual(data["name"], "prompt_injection")
        self.assertIn("description", data)

    def test_load_scenario(self) -> None:
        data = load_preset("scenario", "travel_planner")
        self.assertEqual(data["kind"], "scenario")
        self.assertEqual(data["name"], "travel_planner")
        self.assertIn("context", data)

    def test_load_moved_scenario_as_behavior_builds_legacy_description(self) -> None:
        with self.assertWarns(FutureWarning):
            data = load_preset("behavior", "travel_planner")
        self.assertEqual(data["kind"], "scenario")
        self.assertIn("description", data)
        self.assertIn(data["context"].strip(), data["description"])

    def test_load_kind_mismatch_raises(self) -> None:
        # safety-core is a judge_preset, not a behavior
        with self.assertRaises(ValueError):
            load_preset("behavior", "safety-core")

    def test_judge_dimensions_have_description_and_rubric(self) -> None:
        data = load_preset("judge_preset", "safety-core")
        for dim_name, dim in data["dimensions"].items():
            with self.subTest(dim=dim_name):
                self.assertIn("description", dim)
                self.assertIn("rubric", dim)
                self.assertIsInstance(dim["description"], str)
                self.assertIsInstance(dim["rubric"], str)


class DiscoverTest(unittest.TestCase):
    def test_discover_all(self) -> None:
        results = discover()
        self.assertGreater(len(results), 0)
        kinds_found = {r["kind"] for r in results}
        self.assertEqual(kinds_found, VALID_KINDS)

    def test_discover_judges_only(self) -> None:
        results = discover("judge_preset")
        self.assertGreater(len(results), 0)
        self.assertTrue(all(r["kind"] == "judge_preset" for r in results))

    def test_discover_behaviors_only(self) -> None:
        results = discover("behavior")
        self.assertGreater(len(results), 0)
        self.assertTrue(all(r["kind"] == "behavior" for r in results))

    def test_discover_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            discover("bogus")

    def test_discover_entries_have_required_keys(self) -> None:
        for entry in discover():
            with self.subTest(name=entry.get("name")):
                self.assertIn("kind", entry)
                self.assertIn("name", entry)
                self.assertIn("path", entry)


if __name__ == "__main__":
    unittest.main()
