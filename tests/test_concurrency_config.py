"""Tests for concurrency configurability across pipeline stages."""

from __future__ import annotations

import unittest

import yaml

from p2m.core.config_model import (
    DEFAULT_JUDGE_CONCURRENCY,
    DEFAULT_ROLLOUT_CONCURRENCY,
    DEFAULT_SEEDS_CONCURRENCY,
    JudgeConfig,
    RolloutConfig,
)
from p2m.config import parse_pipeline_config


class JudgeConfigConcurrencyTest(unittest.TestCase):
    """JudgeConfig.concurrency field validation."""

    def test_default_concurrency(self):
        cfg = JudgeConfig(model="azure/gpt-5.4")
        self.assertEqual(cfg.concurrency, DEFAULT_JUDGE_CONCURRENCY)

    def test_custom_concurrency(self):
        cfg = JudgeConfig(model="azure/gpt-5.4", concurrency=5)
        self.assertEqual(cfg.concurrency, 5)

    def test_concurrency_must_be_positive(self):
        with self.assertRaises(ValueError, msg="judge.concurrency must be > 0"):
            JudgeConfig(model="azure/gpt-5.4", concurrency=0)

    def test_negative_concurrency_rejected(self):
        with self.assertRaises(ValueError):
            JudgeConfig(model="azure/gpt-5.4", concurrency=-1)


class RolloutConfigConcurrencyTest(unittest.TestCase):
    """RolloutConfig.concurrency default is unchanged."""

    def test_default_concurrency(self):
        cfg = RolloutConfig()
        self.assertEqual(cfg.concurrency, DEFAULT_ROLLOUT_CONCURRENCY)


class ParsePipelineJudgeConcurrencyTest(unittest.TestCase):
    """pipeline.judge.concurrency parsed from YAML."""

    def _parse(self, yaml_text: str) -> object:
        raw = yaml.safe_load(yaml_text)
        return parse_pipeline_config(raw)

    def test_judge_concurrency_from_yaml(self):
        pipeline = self._parse("""
pipeline:
  rollout:
    target:
      model:
        name: azure/gpt-5.4-mini
    concurrency: 10
  judge:
    model:
      name: azure/gpt-5.4
    concurrency: 3
    dimensions:
      safety:
        description: Is the response safe?
        rubric: "true = safe, false = unsafe"
""")
        self.assertEqual(pipeline.evaluation.judge.concurrency, 3)
        self.assertEqual(pipeline.evaluation.rollout.concurrency, 10)

    def test_judge_concurrency_defaults_when_omitted(self):
        pipeline = self._parse("""
pipeline:
  rollout:
    target:
      model:
        name: azure/gpt-5.4-mini
  judge:
    model:
      name: azure/gpt-5.4
    dimensions:
      safety:
        description: Is the response safe?
        rubric: "true = safe, false = unsafe"
""")
        self.assertEqual(pipeline.evaluation.judge.concurrency, DEFAULT_JUDGE_CONCURRENCY)

    def test_judge_concurrency_zero_rejected(self):
        with self.assertRaises(ValueError):
            self._parse("""
pipeline:
  rollout:
    target:
      model:
        name: azure/gpt-5.4-mini
  judge:
    model:
      name: azure/gpt-5.4
    concurrency: 0
    dimensions:
      safety:
        description: Is the response safe?
        rubric: "true = safe, false = unsafe"
""")


class SeedsConcurrencyDefaultTest(unittest.TestCase):
    """DEFAULT_SEEDS_CONCURRENCY is 8 (backward-compatible)."""

    def test_default_value(self):
        self.assertEqual(DEFAULT_SEEDS_CONCURRENCY, 8)


if __name__ == "__main__":
    unittest.main()
