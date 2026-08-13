# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the Foundry evaluator spec builders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# The `[foundry]` extra (azure-ai-projects) backs every symbol reached
# from this module. Skip cleanly on a base install so CI's Tier 1 job
# (which does not install optional extras) can still collect the file.
pytest.importorskip("azure.ai.projects")

from assert_ai.integrations.foundry.artifacts import AssertRun
from assert_ai.integrations.foundry.evaluators import (
    EVALUATOR_NAME_PREFIX,
    RESCORE_SUFFIX,
    AssertEvaluatorSpec,
    EvaluatorSpecError,
    build_code_evaluator_spec,
    build_evaluator_specs_for_run,
    build_prompt_evaluator_spec,
    evaluator_name_for,
    resolve_rubric_prose,
)


# ── Fixtures ─────────────────────────────────────────────────────────


_UNSET: Any = object()


def _make_run(
    *,
    config: Any = _UNSET,
    scores: Any = _UNSET,
) -> AssertRun:
    default_scores = [
        {
            "test_case_id": "tc-1",
            "judge_model": "azure/gpt-5.4-mini",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": False, "overrefusal": False},
            },
        }
    ]
    default_config: dict[str, Any] = {"default_model": {"name": "azure/gpt-5.4"}}
    return AssertRun(
        run_dir=Path("/tmp/r"),
        suite_dir=Path("/tmp/s"),
        suite_id="s",
        run_id="r",
        taxonomy=None,
        systematization=None,
        stratification=None,
        suite_metadata=None,
        latest=None,
        test_set=(),
        config=default_config if config is _UNSET else config,
        inference_set=(
            {"test_case_id": "tc-1", "type": "prompt", "behavior": "b", "events": []},
        ),
        scores=tuple(default_scores if scores is _UNSET else scores),
        metrics=None,
        manifest=None,
        artifacts_cache=None,
        inference_config_hash=None,
        judge_config_hash=None,
        viewer_files={},
    )


# ── evaluator_name_for ──────────────────────────────────────────────


def test_evaluator_name_code_variant() -> None:
    assert evaluator_name_for("policy_violation", variant="code") == "assert-policy_violation"


def test_evaluator_name_prompt_variant() -> None:
    assert evaluator_name_for("overrefusal", variant="prompt") == "assert-overrefusal-rescore"


def test_evaluator_name_rejects_bad_dimension_id() -> None:
    with pytest.raises(EvaluatorSpecError, match="Invalid dimension id"):
        evaluator_name_for("BadDim", variant="code")


def test_evaluator_name_rejects_leading_digit() -> None:
    with pytest.raises(EvaluatorSpecError):
        evaluator_name_for("1bad", variant="code")


def test_public_constants_exposed() -> None:
    assert EVALUATOR_NAME_PREFIX == "assert-"
    assert RESCORE_SUFFIX == "-rescore"


# ── build_code_evaluator_spec ───────────────────────────────────────


def test_code_spec_shape() -> None:
    spec = build_code_evaluator_spec("policy_violation", description="Check violations.")

    assert isinstance(spec, AssertEvaluatorSpec)
    assert spec.dimension_id == "policy_violation"
    assert spec.variant == "code"
    assert spec.evaluator_name == "assert-policy_violation"


def test_code_spec_registers_as_custom_quality_evaluator() -> None:
    spec = build_code_evaluator_spec("policy_violation", description="x")

    payload = spec.evaluator_version.as_dict()
    assert payload["evaluator_type"] == "custom"
    assert payload["categories"] == ["quality"]
    assert payload["display_name"] == "ASSERT evaluator: policy_violation"
    assert payload["description"] == "x"


def test_code_spec_grader_plucks_pre_computed_score() -> None:
    spec = build_code_evaluator_spec("answer_quality", description="x")

    payload = spec.evaluator_version.as_dict()
    definition = payload["definition"]
    assert definition["type"] == "code"

    code_text = definition["code_text"]
    assert "def grade(sample: dict, item: dict) -> float:" in code_text
    # Contract-critical: reads the pre-normalized ASSERT score, not a raw verdict.
    assert "item.get('assert_scores')" in code_text
    assert "'answer_quality'" in code_text
    # Missing / non-float scores default to 0.0 instead of raising.
    assert "return 0.0" in code_text


def test_code_spec_metric_is_continuous_zero_to_one() -> None:
    spec = build_code_evaluator_spec("policy_violation", description="x")
    metric = spec.evaluator_version.as_dict()["definition"]["metrics"]["result"]

    assert metric["type"] == "continuous"
    assert metric["desirable_direction"] == "increase"
    assert metric["min_value"] == 0.0
    assert metric["max_value"] == 1.0


def test_code_spec_data_schema_declares_assert_scores_as_optional() -> None:
    """Schema declares assert_scores + the dimension without requiring either.

    Data schema declares ``assert_scores`` at the top level (not wrapped
    in an ``item`` object). This matches the eval-side ``data_mapping``:
    ``{"assert_scores": "{{item.assert_scores}}"}`` — Foundry's regex
    validator rejects mapping values that aren't in the ``{{item.foo}}``
    form and rejects mapping keys that don't match a declared schema
    property.

    Both ``assert_scores`` and its inner dimension are declared but
    NOT required. Rows for inference entries whose judge errored will
    carry an empty ``assert_scores`` map (see :func:`build_dataset_rows`
    in ``dataset.py``); we want those rows visible in Foundry as
    un-scored conversations rather than rejected at schema validation.
    """
    spec = build_code_evaluator_spec("policy_violation", description="x")
    schema = spec.evaluator_version.as_dict()["definition"]["data_schema"]

    assert schema["type"] == "object"
    assert "assert_scores" in schema["properties"]
    assert schema["required"] == []
    assert "policy_violation" in schema["properties"]["assert_scores"]["properties"]
    assert schema["properties"]["assert_scores"]["required"] == []


def test_code_spec_init_parameters_are_empty() -> None:
    """Code evaluators don't call any model, so no deployment_name required."""
    spec = build_code_evaluator_spec("policy_violation", description="x")
    init = spec.evaluator_version.as_dict()["definition"]["init_parameters"]

    assert init == {"type": "object", "properties": {}, "required": []}


# ── build_prompt_evaluator_spec ─────────────────────────────────────


def test_prompt_spec_shape() -> None:
    spec = build_prompt_evaluator_spec(
        "policy_violation", description="Check violations.", rubric_prose="rate 1-5"
    )

    assert spec.dimension_id == "policy_violation"
    assert spec.variant == "prompt"
    assert spec.evaluator_name == "assert-policy_violation-rescore"


def test_prompt_spec_registers_with_rescore_display_name() -> None:
    spec = build_prompt_evaluator_spec(
        "overrefusal", description="desc", rubric_prose="rubric"
    )

    payload = spec.evaluator_version.as_dict()
    assert payload["display_name"] == "ASSERT evaluator (re-score): overrefusal"


def test_prompt_spec_template_includes_rubric_and_placeholders() -> None:
    spec = build_prompt_evaluator_spec(
        "answer_quality",
        description="desc",
        rubric_prose="RUBRIC-PROSE-MARKER",
    )
    payload = spec.evaluator_version.as_dict()
    definition = payload["definition"]

    assert definition["type"] == "prompt"
    prompt_text = definition["prompt_text"]
    assert "RUBRIC-PROSE-MARKER" in prompt_text
    # Foundry data mapping binds these placeholders per row.
    assert "{{query}}" in prompt_text
    assert "{{response}}" in prompt_text
    # Ordinal 1-5 output format.
    assert '"result": <integer 1 to 5' in prompt_text
    assert '"reason"' in prompt_text


def test_prompt_spec_metric_is_ordinal_one_to_five() -> None:
    spec = build_prompt_evaluator_spec("policy_violation", description="x", rubric_prose="r")
    metric = spec.evaluator_version.as_dict()["definition"]["metrics"]["result"]

    assert metric["type"] == "ordinal"
    assert metric["desirable_direction"] == "increase"
    assert metric["min_value"] == 1
    assert metric["max_value"] == 5


def test_prompt_spec_data_schema_requires_query_and_response() -> None:
    spec = build_prompt_evaluator_spec("policy_violation", description="x", rubric_prose="r")
    schema = spec.evaluator_version.as_dict()["definition"]["data_schema"]

    assert schema["properties"] == {
        "query": {"type": "string"},
        "response": {"type": "string"},
    }
    assert sorted(schema["required"]) == ["query", "response"]


def test_prompt_spec_init_parameters_require_deployment_and_threshold() -> None:
    spec = build_prompt_evaluator_spec("policy_violation", description="x", rubric_prose="r")
    init = spec.evaluator_version.as_dict()["definition"]["init_parameters"]

    assert init["properties"]["deployment_name"]["type"] == "string"
    assert init["properties"]["threshold"]["type"] == "number"
    assert sorted(init["required"]) == ["deployment_name", "threshold"]


# ── resolve_rubric_prose ────────────────────────────────────────────


def test_rubric_inline_config_wins() -> None:
    assert (
        resolve_rubric_prose("policy_violation", inline_rubrics={"policy_violation": "MY-RUBRIC"})
        == "MY-RUBRIC"
    )


def test_rubric_built_in_fallback_for_policy_violation() -> None:
    prose = resolve_rubric_prose("policy_violation", inline_rubrics={})

    assert "Score 1-5" in prose
    assert "violated any relevant behavior" in prose


def test_rubric_built_in_fallback_for_overrefusal() -> None:
    prose = resolve_rubric_prose("overrefusal", inline_rubrics={})

    assert "overrefused" in prose


def test_rubric_generic_fallback_for_unknown_dimension() -> None:
    prose = resolve_rubric_prose("tone_appropriateness", inline_rubrics={})

    assert "tone_appropriateness" in prose
    assert "not exported with the run" in prose


# ── build_evaluator_specs_for_run ───────────────────────────────────


def test_specs_for_run_default_mode_emits_both_variants() -> None:
    specs = build_evaluator_specs_for_run(_make_run())

    # Two dimensions × 2 variants = 4 specs.
    assert len(specs) == 4
    names = [s.evaluator_name for s in specs]
    # Alphabetical by dim, code before prompt within a dim.
    assert names == [
        "assert-overrefusal",
        "assert-overrefusal-rescore",
        "assert-policy_violation",
        "assert-policy_violation-rescore",
    ]


def test_specs_for_run_code_mode_omits_prompt_variants() -> None:
    specs = build_evaluator_specs_for_run(_make_run(), mode="code")

    assert [s.variant for s in specs] == ["code", "code"]
    assert [s.evaluator_name for s in specs] == [
        "assert-overrefusal",
        "assert-policy_violation",
    ]


def test_specs_for_run_prompt_mode_omits_code_variants() -> None:
    specs = build_evaluator_specs_for_run(_make_run(), mode="prompt")

    assert [s.variant for s in specs] == ["prompt", "prompt"]
    assert [s.evaluator_name for s in specs] == [
        "assert-overrefusal-rescore",
        "assert-policy_violation-rescore",
    ]


def test_specs_for_run_includes_custom_dimensions_from_scores() -> None:
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {
                    "policy_violation": False,
                    "overrefusal": False,
                    "answer_quality": True,
                }
            },
        }
    ]
    config = {
        "pipeline": {
            "judge": {
                "dimensions": {
                    "answer_quality": {
                        "description": "Did it answer?",
                        "rubric": "true = yes",
                    }
                }
            }
        }
    }

    specs = build_evaluator_specs_for_run(
        _make_run(config=config, scores=scores), mode="prompt"
    )

    aq_spec = next(s for s in specs if s.dimension_id == "answer_quality")
    prompt_text = aq_spec.evaluator_version.as_dict()["definition"]["prompt_text"]
    assert "Did it answer?" in prompt_text
    assert "true = yes" in prompt_text


def test_specs_for_run_empty_when_no_scored_dimensions() -> None:
    scores = [{"test_case_id": "tc-1", "judge_status": "ok", "verdict": {}}]

    assert build_evaluator_specs_for_run(_make_run(scores=scores)) == []


def test_specs_for_run_rejects_unknown_mode() -> None:
    with pytest.raises(EvaluatorSpecError, match="Unknown evaluator mode"):
        build_evaluator_specs_for_run(_make_run(), mode="bogus")  # type: ignore[arg-type]


# ── Lazy load via package root ──────────────────────────────────────


def test_lazy_load_via_package_root() -> None:
    import assert_ai.integrations.foundry as foundry

    assert foundry.build_code_evaluator_spec is build_code_evaluator_spec
    assert foundry.build_prompt_evaluator_spec is build_prompt_evaluator_spec
    assert foundry.build_evaluator_specs_for_run is build_evaluator_specs_for_run
    assert foundry.evaluator_name_for is evaluator_name_for
    assert foundry.resolve_rubric_prose is resolve_rubric_prose
    assert foundry.AssertEvaluatorSpec is AssertEvaluatorSpec
    assert foundry.EvaluatorSpecError is EvaluatorSpecError
    assert foundry.EVALUATOR_NAME_PREFIX == EVALUATOR_NAME_PREFIX
    assert foundry.RESCORE_SUFFIX == RESCORE_SUFFIX
