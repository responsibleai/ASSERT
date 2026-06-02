# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for behavior.preset and judge.preset integration in config loading."""
import unittest
from pathlib import Path

from assert_ai.config import load_runtime_context
from assert_ai.stages import STAGES


def _base_config(**overrides):
    """Minimal valid config dict with overrides merged in."""
    cfg = {
        "suite": "test-suite",
        "behavior": {"name": "test_behavior"},
        "pipeline": {
            "test_set": {"prompt": {"model": {"name": "azure/gpt-5.4"}}},
            "inference": {"target": {"model": {"name": "azure/gpt-5.4"}}},
            "judge": {"model": {"name": "azure/gpt-5.4"}},
        },
    }
    cfg.update(overrides)
    return cfg


class BehaviorPresetTest(unittest.TestCase):
    def _load(self, behavior_dict):
        cfg = _base_config(behavior=behavior_dict)
        return load_runtime_context(cfg, Path("test.yaml"), stage_modules=STAGES)

    def test_preset_derives_name_and_description(self):
        ctx = self._load({"preset": "harmful_medical_advice"})
        self.assertEqual(ctx["behavior_name"], "harmful_medical_advice")
        self.assertTrue(len(ctx["behavior"]) > 0, "description should be loaded from preset")

    def test_preset_name_overridden_by_inline(self):
        ctx = self._load({"preset": "harmful_medical_advice", "name": "custom_name"})
        self.assertEqual(ctx["behavior_name"], "custom_name")

    def test_preset_description_overridden_by_inline(self):
        ctx = self._load({"preset": "harmful_medical_advice", "description": "my custom description"})
        self.assertEqual(ctx["behavior"], "my custom description")

    def test_no_preset_still_requires_name(self):
        with self.assertRaises(ValueError):
            self._load({"description": "some desc"})

    def test_invalid_preset_raises(self):
        with self.assertRaises(ValueError):
            self._load({"preset": "nonexistent_preset_xyz"})


class JudgePresetTest(unittest.TestCase):
    def _load(self, judge_overrides):
        judge = {"model": {"name": "azure/gpt-5.4"}}
        judge.update(judge_overrides)
        cfg = _base_config()
        cfg["pipeline"]["judge"] = judge
        return load_runtime_context(cfg, Path("test.yaml"), stage_modules=STAGES)

    def _judge_dims(self, ctx):
        return ctx["evaluation"].judge.dimensions

    def test_preset_loads_dimensions(self):
        ctx = self._load({"preset": "safety-core"})
        dims = self._judge_dims(ctx)
        self.assertTrue(len(dims) > 0, "should load dimensions from preset")

    def test_inline_dims_override_preset_dims(self):
        ctx = self._load({
            "preset": "safety-core",
            "dimensions": {
                "policy_violation": {
                    "description": "custom desc",
                    "rubric": "custom rubric",
                },
            },
        })
        dim = next(d for d in self._judge_dims(ctx) if d["name"] == "policy_violation")
        self.assertEqual(dim["description"], "custom desc")
        self.assertEqual(dim["rubric"], "custom rubric")

    def test_inline_dims_merged_with_preset(self):
        ctx = self._load({
            "preset": "safety-core",
            "dimensions": {
                "extra_dimension": {
                    "description": "extra desc",
                    "rubric": "extra rubric",
                },
            },
        })
        dim_names = [d["name"] for d in self._judge_dims(ctx)]
        self.assertIn("extra_dimension", dim_names)
        # Preset dims (policy_violation, overrefusal) should also be present
        self.assertTrue(len(dim_names) >= 3)

    def test_invalid_preset_raises(self):
        with self.assertRaises(ValueError):
            self._load({"preset": "nonexistent_judge_xyz"})


if __name__ == "__main__":
    unittest.main()
