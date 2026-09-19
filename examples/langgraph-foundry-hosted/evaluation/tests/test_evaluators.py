# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

import pytest

from evaluators import EvaluatorError, compile_evaluator_specs, resolved_boolean_dimensions


EXAMPLE_DIR = Path(__file__).resolve().parents[2]


def _write_config(path: Path, judge: str) -> Path:
    path.write_text(
        f"""suite: demo
run: baseline
default_model:
  name: azure/gpt-5.4
pipeline:
  judge:
{judge}
""",
        encoding="utf-8",
    )
    return path


def test_resolves_presets_inline_overrides_and_disabled_builtins() -> None:
    dimensions = resolved_boolean_dimensions(EXAMPLE_DIR / "eval_config.yaml")
    by_name = {dimension["name"]: dimension for dimension in dimensions}
    assert set(by_name) == {"policy_violation", "overrefusal"}
    assert "wrong tools" in by_name["policy_violation"]["rubric"]


def test_disabled_builtin_is_omitted(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path / "config.yaml",
        """    disabled_dimensions: [overrefusal]
    dimensions:
      custom_bad_event:
        description: Did a custom failure occur?
        rubric: |-
          true = failure
          false = no failure
""",
    )
    names = [dimension["name"] for dimension in resolved_boolean_dimensions(config)]
    assert names == ["policy_violation", "custom_bad_event"]


def test_disabled_dimension_is_applied_after_preset_resolution(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path / "config.yaml",
        """    preset: safety-core
    disabled_dimensions: [overrefusal]
""",
    )
    names = [dimension["name"] for dimension in resolved_boolean_dimensions(config)]
    assert names == ["policy_violation"]


def test_exact_rubric_and_boolean_inversion_are_preserved(tmp_path: Path) -> None:
    rubric = "true = exact bad event\\nfalse = exact absence"
    config = _write_config(
        tmp_path / "config.yaml",
        f"""    disabled_dimensions: [policy_violation, overrefusal]
    dimensions:
      exact:
        description: Exact dimension
        rubric: |-
          {rubric.replace(chr(10), chr(10) + '          ')}
""",
    )
    spec = compile_evaluator_specs(config, level="turn")[0]
    prompt = spec.payload["definition"]["prompt_text"]
    metric = spec.payload["definition"]["metrics"]["assert_pass"]
    assert rubric in prompt
    assert "ASSERT verdict would be false" in prompt
    assert spec.payload["metadata"]["assert_boolean_inverted"] == "true"
    assert metric["type"] == "boolean"
    assert metric["desirable_direction"] == "increase"


def test_prompt_includes_assert_behavior_and_target_context() -> None:
    spec = compile_evaluator_specs(EXAMPLE_DIR / "eval_config.yaml", level="turn")[0]
    prompt = spec.payload["definition"]["prompt_text"]
    assert "Travel Planner Evaluation" in prompt
    assert "multi-agent LangGraph travel planner" in prompt


def test_slugs_are_deterministic_and_collision_safe(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path / "config.yaml",
        """    disabled_dimensions: [policy_violation, overrefusal]
    dimensions:
      "a b":
        description: First
        rubric: "true = bad; false = good"
      "a-b":
        description: Second
        rubric: "true = bad; false = good"
""",
    )
    first = compile_evaluator_specs(config, level="turn")
    second = compile_evaluator_specs(config, level="turn")
    assert [spec.name for spec in first] == [spec.name for spec in second]
    assert first[0].name != first[1].name


def test_ordinal_dimensions_fail_loudly(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path / "config.yaml",
        """    dimensions:
      severity:
        description: Severity
        rubric: Grade severity
        scale:
          type: ordinal
          values:
            1: low
            2: high
""",
    )
    with pytest.raises(EvaluatorError, match="ordinal scale"):
        compile_evaluator_specs(config, level="turn")


def test_not_applicable_boolean_fails_loudly(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path / "config.yaml",
        """    dimensions:
      applicability:
        description: Sometimes applicable
        rubric: "true = bad; false = good"
        allow_not_applicable: true
""",
    )
    with pytest.raises(EvaluatorError, match="not-applicable"):
        compile_evaluator_specs(config, level="turn")


def test_level_specific_contracts_map_correct_schema() -> None:
    turn = compile_evaluator_specs(EXAMPLE_DIR / "eval_config.yaml", level="turn")[0]
    conversation = compile_evaluator_specs(
        EXAMPLE_DIR / "eval_config.yaml", level="conversation"
    )[0]
    assert turn.name != conversation.name
    assert turn.payload["supported_evaluation_levels"] == ["turn"]
    assert conversation.payload["supported_evaluation_levels"] == ["conversation"]
    assert turn.payload["definition"]["data_schema"]["required"] == [
        "query",
        "response",
        "tool_evidence",
    ]
    assert conversation.payload["definition"]["data_schema"]["required"] == ["messages"]
