# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for library.loader — preset discovery and loading."""

import unittest

from p2m.library.loader import (
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
        path = resolve_preset("behavior", "travel_planner")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "travel_planner.yaml")

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
        data = load_preset("behavior", "travel_planner")
        self.assertEqual(data["kind"], "behavior")
        self.assertEqual(data["name"], "travel_planner")
        self.assertIn("description", data)

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
