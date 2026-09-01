# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Best-effort pre-run token estimates for configured pipeline stages."""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from assert_ai.config import parse_model_config, resolve_stage_paths
from assert_ai.core.artifact_cache import _was_cached_artifact, file_sha256
from assert_ai.core.config_model import (
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_GENERATION_TEMPERATURE,
    DEFAULT_INFERENCE_MAX_TOKENS,
    DEFAULT_JUDGE_MAX_TOKENS,
    DEFAULT_SYSTEMATIZE_MAX_TOKENS,
    DEFAULT_SYSTEMATIZE_TEMPERATURE,
    EvaluationConfig,
    ModelConfig,
    TargetConfig,
)
from assert_ai.core.io import (
    INFERENCE_SET_FILE,
    SCORES_FILE,
    fill_template,
    load_jsonl,
    normalize_test_case_rows,
    normalize_test_case_context,
    row_factors,
)
from assert_ai.core.judge import NODE_JUDGMENTS_KEY, build_judge_contract
from assert_ai.core.model_client import Message, ToolCall, estimate_token_count
from assert_ai.core.tools import (
    build_target_tools,
    load_toolset_file,
    normalize_tool_defs,
    resolve_toolset_path,
)
from assert_ai.core.transcript import (
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)
from assert_ai.stages import inference as inference_stage
from assert_ai.stages import judge as judge_stage
from assert_ai.stages import stratification as stratification_stage
from assert_ai.stages import systematization
from assert_ai.stages import systematization_convert
from assert_ai.stages import systematize
from assert_ai.stages import test_set

_MAX_PROFILE_SAMPLES = 24
_TESTER_OUTPUT_TOKENS = 55
_PROMPT_TARGET_OUTPUT_TOKENS = 350
_SCENARIO_TARGET_OUTPUT_TOKENS = 180
_SIMULATOR_OUTPUT_TOKENS = 90
_UNSCORABLE_STOP_REASONS = {
    "tester_input_refused",
    "target_input_refused",
    "target_error",
    "tester_error",
}
_T = TypeVar("_T")


@dataclass(slots=True)
class StageTokenEstimate:
    """Estimated tracked usage for one pipeline stage."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class PipelineTokenEstimate:
    """Aggregate pre-run token estimate with an explicit uncertainty range."""

    stages: dict[str, StageTokenEstimate] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(stage.calls for stage in self.stages.values())

    @property
    def input_tokens(self) -> int:
        return sum(stage.input_tokens for stage in self.stages.values())

    @property
    def output_tokens(self) -> int:
        return sum(stage.output_tokens for stage in self.stages.values())

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncertainty(self) -> float:
        return 0.30 if self.notes else 0.20

    @property
    def lower_bound_tokens(self) -> int:
        return max(0, round(self.total_tokens * (1.0 - self.uncertainty)))

    @property
    def upper_bound_tokens(self) -> int:
        return round(self.total_tokens * (1.0 + self.uncertainty))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "lower_bound_tokens": self.lower_bound_tokens,
            "upper_bound_tokens": self.upper_bound_tokens,
            "stages": {
                name: estimate.to_dict()
                for name, estimate in self.stages.items()
            },
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class _CaseProfile:
    kind: str
    test_case_id: str
    description: str
    system_prompt: str | None = None
    tools: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True)
class _CaseInventory:
    samples: dict[str, list[_CaseProfile]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True, slots=True)
class _TranscriptProfile:
    kind: str
    test_case_id: str
    transcript_xml: str


@dataclass(slots=True)
class _TranscriptInventory:
    samples: dict[str, list[_TranscriptProfile]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class _InferenceProjection:
    estimate: StageTokenEstimate
    transcripts: _TranscriptInventory
    pending_cases: int = 0
    notes: list[str] = field(default_factory=list)


def _synthetic_text(tokens: int, label: str = "detail") -> str:
    """Return predictable prose that tokenizes close to one token per word."""
    return " ".join([label] * max(1, tokens))


def _bounded_output(expected: int, max_tokens: int | None) -> int:
    if max_tokens is None:
        return max(1, expected)
    return max(1, min(expected, max_tokens))


def _request_tokens(
    model: str,
    messages: str | Sequence[Message | dict[str, Any]],
    *,
    response_schema: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    total = estimate_token_count(model, messages=messages, tools=tools)
    if response_schema is not None:
        total += estimate_token_count(
            model,
            text=json.dumps(
                response_schema,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    return total


def _sample_evenly(
    items: list[_T],
    limit: int = _MAX_PROFILE_SAMPLES,
) -> list[_T]:
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return [items[0]]
    return [
        items[round(index * (len(items) - 1) / (limit - 1))]
        for index in range(limit)
    ]


def _scaled_sum(
    samples: list[_T],
    total_count: int,
    estimator: Callable[[_T], int],
) -> int:
    if not samples or total_count <= 0:
        return 0
    measured = [estimator(item) for item in _sample_evenly(samples)]
    return round(sum(measured) / len(measured) * total_count)


def _resolved_path(
    ctx: dict[str, Any],
    key: str,
    value: Any,
) -> Path:
    resolved = resolve_stage_paths(
        {key: value},
        cfg_path=Path(ctx["config_path"]),
        artifacts_root=Path(ctx["artifacts_root"]),
    )
    return Path(resolved[key])


def _compatibility_path_will_refresh(
    ctx: dict[str, Any],
    *,
    stage_name: str,
    input_path: Path,
    filename: str,
) -> bool:
    artifact_ref = (ctx.get("artifact_versions") or {}).get(stage_name)
    compatibility_path = (Path(ctx["suite_root"]) / filename).resolve()
    input_path = input_path.resolve()
    if not isinstance(artifact_ref, dict) or input_path != compatibility_path:
        return False
    if not compatibility_path.exists() or not compatibility_path.is_file():
        return True
    try:
        compatibility_hash = file_sha256(compatibility_path)
    except OSError:
        return True
    return _was_cached_artifact(
        Path(ctx["suite_root"]),
        stage_name,
        filename,
        compatibility_hash,
    )


def _systematize_output_feeds_taxonomy(
    ctx: dict[str, Any],
    stage_cfgs: dict[str, dict[str, Any]],
    taxonomy_stage: str,
) -> bool:
    systematize_cfg = stage_cfgs.get("systematize")
    downstream_cfg = stage_cfgs.get(taxonomy_stage)
    if systematize_cfg is None or downstream_cfg is None:
        return False
    output_dir = _resolved_path(
        ctx,
        "save_dir",
        str(
            systematize_cfg.get("save_dir")
            or ctx.get("systematize_artifact_dir")
            or ctx["suite_root"]
        ),
    )
    taxonomy_input = _resolved_path(
        ctx,
        "taxonomy_path",
        str(
            downstream_cfg.get("taxonomy_path")
            or ctx.get("taxonomy_path")
            or Path(ctx["suite_root"]) / "taxonomy.json"
        ),
    )
    return (
        output_dir / "taxonomy.json" == taxonomy_input
        or _compatibility_path_will_refresh(
            ctx,
            stage_name="systematize",
            input_path=taxonomy_input,
            filename="taxonomy.json",
        )
    )


def _load_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _synthetic_taxonomy(
    ctx: dict[str, Any],
    stage_cfgs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    systematize_cfg = stage_cfgs.get("systematize") or {}
    category_count = systematize_cfg.get(
        "behavior_category_count",
        systematize.DEFAULT_BEHAVIOR_CATEGORY_COUNT,
    )
    if not isinstance(category_count, int) or category_count <= 0:
        category_count = systematize.DEFAULT_BEHAVIOR_CATEGORY_COUNT
    behavior_name = str(ctx.get("behavior_name") or "behavior")
    behavior_description = str(ctx.get("behavior") or "Behavior under evaluation")
    categories = []
    for index in range(category_count):
        categories.append(
            {
                "name": f"category_{index + 1}",
                "definition": _synthetic_text(42, "definition"),
                "examples": [
                    _synthetic_text(18, "example"),
                    _synthetic_text(18, "example"),
                ],
                "permissible": index % 3 == 0,
            }
        )
    return {
        "behavior": {
            "name": behavior_name,
            "definition": behavior_description,
        },
        "definition_of_terms": [
            {
                "term": "representative term",
                "definition": _synthetic_text(24, "definition"),
                "examples": [_synthetic_text(12, "example")],
            }
        ],
        "behavior_categories": categories,
    }


def _taxonomy_for_stage(
    ctx: dict[str, Any],
    stage_cfgs: dict[str, dict[str, Any]],
    stage_name: str,
) -> tuple[dict[str, Any], bool]:
    if _systematize_output_feeds_taxonomy(ctx, stage_cfgs, stage_name):
        return _synthetic_taxonomy(ctx, stage_cfgs), True
    stage_cfg = stage_cfgs.get(stage_name) or {}
    raw_path = (
        stage_cfg.get("taxonomy_path")
        or ctx.get("taxonomy_path")
        or str(Path(ctx["suite_root"]) / "taxonomy.json")
    )
    taxonomy = _load_json_mapping(_resolved_path(ctx, "taxonomy_path", raw_path))
    if taxonomy is not None and taxonomy.get("behavior_categories"):
        return taxonomy, False
    return _synthetic_taxonomy(ctx, stage_cfgs), True


def _synthetic_systematization(
    behavior_name: str,
    category_count: int,
) -> dict[str, Any]:
    pattern_count = max(5, min(category_count, 40))
    systematization_text = "\n\n".join(
        f"Pattern {index + 1}: {_synthetic_text(78, 'pattern')}"
        for index in range(pattern_count)
    )
    return {
        "systematization": systematization_text,
        "summary_items": [
            {
                "description": _synthetic_text(28, "summary"),
                "example": _synthetic_text(20, "example"),
            }
            for _ in range(pattern_count)
        ],
        "behavior": behavior_name,
    }


def _estimate_systematize(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
) -> StageTokenEstimate:
    model_raw = raw_cfg.get("model")
    if not isinstance(model_raw, dict):
        raise ValueError("systematize.model must be a mapping")
    model_cfg = parse_model_config(
        model_raw,
        field_name="systematize.model",
        default_temperature=DEFAULT_SYSTEMATIZE_TEMPERATURE,
        default_max_tokens=DEFAULT_SYSTEMATIZE_MAX_TOKENS,
    )
    category_count = raw_cfg.get(
        "behavior_category_count",
        systematize.DEFAULT_BEHAVIOR_CATEGORY_COUNT,
    )
    if not isinstance(category_count, int) or category_count <= 0:
        category_count = systematize.DEFAULT_BEHAVIOR_CATEGORY_COUNT
    behavior_name = str(ctx.get("behavior_name") or "behavior")
    behavior_text = str(ctx.get("behavior") or "")
    context = ctx.get("context")

    first_prompt = systematization._build_prompt(
        behavior=behavior_name,
        behavior_text=behavior_text,
        context=context if isinstance(context, str) else None,
    )
    first_schema = systematization.SystematizationResponse.model_json_schema()
    first_input = _request_tokens(
        model_cfg.name,
        first_prompt,
        response_schema=first_schema,
    )
    synthetic = _synthetic_systematization(behavior_name, category_count)
    first_output = _bounded_output(
        estimate_token_count(
            model_cfg.name,
            text=json.dumps(synthetic, ensure_ascii=False),
        ),
        model_cfg.max_tokens,
    )

    second_prompt = (
        systematization_convert.GUIDELINE_PROMPT.replace(
            "{{behavior_category_count}}",
            str(category_count),
        )
        + "\n\n# SYSTEMATIZATION\n"
        + str(synthetic["systematization"])
        + "\n\n# SUMMARY ITEMS\n"
        + json.dumps(synthetic["summary_items"], ensure_ascii=False, indent=2)
    )
    second_input = _request_tokens(
        model_cfg.name,
        second_prompt,
        response_schema=systematization_convert.TAXONOMY_SCHEMA,
    )
    taxonomy_output = _bounded_output(
        estimate_token_count(
            model_cfg.name,
            text=json.dumps(
                _synthetic_taxonomy(
                    ctx,
                    {"systematize": {"behavior_category_count": category_count}},
                ),
                ensure_ascii=False,
            ),
        ),
        model_cfg.max_tokens,
    )
    return StageTokenEstimate(
        calls=2,
        input_tokens=first_input + second_input,
        output_tokens=first_output + taxonomy_output,
    )


def _stratification_for_plan(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    taxonomy: dict[str, Any],
) -> tuple[dict[str, Any], StageTokenEstimate | None]:
    raw_path = (
        ctx.get("stratification_path")
        or str(Path(ctx["suite_root"]) / "stratification.json")
    )
    existing = _load_json_mapping(_resolved_path(ctx, "stratification_path", raw_path))
    if existing is not None:
        return existing, None

    stratify_raw = raw_cfg.get("stratify") or {}
    if not isinstance(stratify_raw, dict):
        stratify_raw = {}
    dimensions = stratify_raw.get("dimensions", ctx.get("dimensions")) or []
    if not isinstance(dimensions, list):
        dimensions = []
    level_count = stratify_raw.get(
        "level_count",
        stratification_stage.DEFAULT_LEVEL_COUNT,
    )
    if not isinstance(level_count, int) or level_count <= 0:
        level_count = stratification_stage.DEFAULT_LEVEL_COUNT

    raw_stratification: dict[str, Any] = {}
    missing_dimensions: list[dict[str, Any]] = []
    factor_order: list[str] = []
    for index, dimension in enumerate(dimensions):
        if not isinstance(dimension, dict):
            continue
        name = str(dimension.get("name") or f"dimension_{index + 1}")
        factor_order.append(name)
        levels = dimension.get("levels")
        if isinstance(levels, list) and levels:
            raw_stratification[name] = levels
            continue
        missing_dimensions.append(
            {
                "name": name,
                "description": str(
                    dimension.get("description")
                    or _synthetic_text(32, "dimension")
                ),
            }
        )
        raw_stratification[name] = [
            {
                "name": f"{name}_level_{level_index + 1}",
                "definition": _synthetic_text(22, "level"),
            }
            for level_index in range(level_count)
        ]

    normalized = stratification_stage.normalize_stratification(
        raw_stratification,
        taxonomy,
        factor_order=factor_order,
        inject_behavior=True,
    )
    if not missing_dimensions:
        return normalized, None

    model_raw = stratify_raw.get("model") or raw_cfg.get("model")
    if not isinstance(model_raw, dict):
        return normalized, None
    model_cfg = parse_model_config(
        model_raw,
        field_name="test_set.stratify.model",
    )
    normalized_context = normalize_test_case_context(ctx.get("context"))
    prompt = fill_template(
        stratification_stage.STRATIFICATION_PROMPT_TEMPLATE,
        {
            "behavior_name": str(
                taxonomy.get("behavior", {}).get("name") or "behavior"
            ),
            "behavior_categories": (
                stratification_stage.render_behavior_categories(taxonomy)
            ),
            "context": normalized_context or "- (no additional context provided)",
            "factors_section": (
                stratification_stage.render_factors_section(missing_dimensions)
            ),
        },
    )
    schema = stratification_stage._stratification_response_schema(
        level_count,
        dimensions=tuple(item["name"] for item in missing_dimensions),
    )
    output_payload = {
        item["name"]: raw_stratification[item["name"]]
        for item in missing_dimensions
    }
    return normalized, StageTokenEstimate(
        calls=1,
        input_tokens=_request_tokens(
            model_cfg.name,
            prompt,
            response_schema=schema,
        ),
        output_tokens=estimate_token_count(
            model_cfg.name,
            text=json.dumps(output_payload, ensure_ascii=False),
        ),
    )


def _synthetic_test_case_payload(
    kind: str,
    *,
    tool_source: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _synthetic_text(7, "title"),
        "description": _synthetic_text(
            180 if kind == "scenario" else 70,
            "scenario" if kind == "scenario" else "prompt",
        ),
        "system_prompt": _synthetic_text(
            90 if kind == "scenario" else 35,
            "instruction",
        ),
    }
    if tool_source == test_set.TOOL_SOURCE_PER_TEST_CASE:
        payload["tools"] = [
            {
                "name": "lookup_record",
                "description": _synthetic_text(24, "tool"),
                "parameters": [
                    {
                        "name": "query",
                        "type": "string",
                        "description": _synthetic_text(18, "parameter"),
                    }
                ],
            },
            {
                "name": "submit_action",
                "description": _synthetic_text(24, "tool"),
                "parameters": [
                    {
                        "name": "value",
                        "type": "string",
                        "description": _synthetic_text(18, "parameter"),
                    }
                ],
            },
        ]
    return payload


def _normalized_test_set_tool_source(raw_cfg: dict[str, Any]) -> str:
    tool_source = str(
        raw_cfg.get("tool_source", test_set.TOOL_SOURCE_RUNTIME)
    )
    if tool_source == test_set.TOOL_SOURCE_PER_TEST_CASE_LEGACY:
        return test_set.TOOL_SOURCE_PER_TEST_CASE
    return tool_source


def _estimate_test_set(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    taxonomy: dict[str, Any],
) -> StageTokenEstimate:
    stratification, stratification_estimate = _stratification_for_plan(
        ctx,
        raw_cfg,
        taxonomy,
    )
    estimate = stratification_estimate or StageTokenEstimate()
    tool_source = _normalized_test_set_tool_source(raw_cfg)

    kind_configs: list[tuple[str, dict[str, Any]]] = []
    if raw_cfg.get("prompt") and isinstance(raw_cfg.get("prompt"), dict):
        kind_configs.append(
            (
                "prompt",
                test_set._parse_kind_config(
                    raw_cfg,
                    "prompt",
                    raw_cfg["prompt"],
                    sample_size=100,
                    temperature=DEFAULT_GENERATION_TEMPERATURE,
                    max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
                ),
            )
        )
    if raw_cfg.get("scenario") and isinstance(raw_cfg.get("scenario"), dict):
        kind_configs.append(
            (
                "scenario",
                test_set._parse_kind_config(
                    raw_cfg,
                    "scenario",
                    raw_cfg["scenario"],
                    sample_size=100,
                    temperature=DEFAULT_GENERATION_TEMPERATURE,
                    max_tokens=DEFAULT_GENERATION_MAX_TOKENS,
                ),
            )
        )

    for kind, kind_cfg in kind_configs:
        jobs, _ = test_set.build_generation_jobs(
            taxonomy=taxonomy,
            stratification=stratification,
            sample_size=int(kind_cfg["sample_size"]),
            rng=random.Random(0),
            sampling=kind_cfg.get("sampling"),
        )
        sampled_jobs = _sample_evenly(jobs)
        sampled_input = []
        sampled_output = []
        for job in sampled_jobs:
            prompt = test_set.build_generation_prompt(
                kind=kind,
                taxonomy=taxonomy,
                behavior=job.behavior,
                count=job.count,
                context=ctx.get("context"),
                stratification=stratification,
                tuple_spec=job.tuple_spec,
                tool_source=tool_source,
            )
            schema = test_set.test_set_response_schema(
                tool_source,
                min_items=job.count,
                max_items=job.count,
            )
            sampled_input.append(
                _request_tokens(
                    str(kind_cfg["model"]),
                    prompt,
                    response_schema=schema,
                )
            )
            output_payload = {
                "test_set": [
                    _synthetic_test_case_payload(
                        kind,
                        tool_source=tool_source,
                    )
                    for _ in range(job.count)
                ]
            }
            sampled_output.append(
                _bounded_output(
                    estimate_token_count(
                        str(kind_cfg["model"]),
                        text=json.dumps(output_payload, ensure_ascii=False),
                    ),
                    int(kind_cfg["max_tokens"])
                    if kind_cfg.get("max_tokens") is not None
                    else None,
                )
            )
        if sampled_jobs:
            scale = len(jobs) / len(sampled_jobs)
            estimate.calls += len(jobs)
            estimate.input_tokens += round(sum(sampled_input) * scale)
            estimate.output_tokens += round(sum(sampled_output) * scale)
    return estimate


def _profile_from_row(row: dict[str, Any], index: int) -> _CaseProfile | None:
    kind = str(row.get("type") or "")
    seed = row.get("seed")
    if kind not in {"prompt", "scenario"} or not isinstance(seed, dict):
        return None
    raw_tools = seed.get("tools")
    tools = tuple(item for item in raw_tools if isinstance(item, dict)) if isinstance(raw_tools, list) else ()
    return _CaseProfile(
        kind=kind,
        test_case_id=str(row.get("test_case_id") or f"estimated_{index + 1}"),
        description=str(seed.get("description") or ""),
        system_prompt=str(seed.get("system_prompt") or "").strip() or None,
        tools=tools,
    )


def _case_inventory(
    ctx: dict[str, Any],
    stage_cfgs: dict[str, dict[str, Any]],
    *,
    prefer_generated: bool = False,
) -> _CaseInventory:
    inference_cfg = stage_cfgs.get("inference") or {}
    test_set_cfg = stage_cfgs.get("test_set") or {}
    raw_path = (
        inference_cfg.get("test_set_path")
        or ctx.get("test_set_path")
        or str(Path(ctx["suite_root"]) / test_set.TEST_SET_FILE)
    )
    rows = normalize_test_case_rows(
        load_jsonl(_resolved_path(ctx, "test_set_path", raw_path))
    )
    inventory = _CaseInventory()
    if rows and not prefer_generated:
        profiles_by_kind: dict[str, list[_CaseProfile]] = {
            "prompt": [],
            "scenario": [],
        }
        for index, row in enumerate(rows):
            profile = _profile_from_row(row, index)
            if profile is None:
                continue
            profiles_by_kind[profile.kind].append(profile)
        for kind, profiles in profiles_by_kind.items():
            if not profiles:
                continue
            inventory.counts[kind] = len(profiles)
            inventory.samples[kind] = profiles
        return inventory

    for kind in ("prompt", "scenario"):
        kind_cfg = test_set_cfg.get(kind)
        if not kind_cfg or not isinstance(kind_cfg, dict):
            continue
        count = kind_cfg.get("sample_size", 100)
        if not isinstance(count, int) or count <= 0:
            continue
        payload = _synthetic_test_case_payload(
            kind,
            tool_source=_normalized_test_set_tool_source(test_set_cfg),
        )
        raw_tools = payload.get("tools")
        inventory.counts[kind] = count
        inventory.samples[kind] = [
            _CaseProfile(
                kind=kind,
                test_case_id=f"estimated_{kind}",
                description=str(payload["description"]),
                system_prompt=str(payload["system_prompt"]),
                tools=(
                    tuple(raw_tools)
                    if isinstance(raw_tools, list)
                    else ()
                ),
            )
        ]
    return inventory


def _test_set_output_feeds_inference(
    ctx: dict[str, Any],
    stage_cfgs: dict[str, dict[str, Any]],
) -> bool:
    test_set_cfg = stage_cfgs.get("test_set")
    inference_cfg = stage_cfgs.get("inference")
    if test_set_cfg is None or inference_cfg is None:
        return False
    test_set_output = _resolved_path(
        ctx,
        "save_path",
        str(
            test_set_cfg.get("save_path")
            or ctx.get("test_set_path")
            or Path(ctx["suite_root"]) / test_set.TEST_SET_FILE
        ),
    )
    inference_input = _resolved_path(
        ctx,
        "test_set_path",
        str(
            inference_cfg.get("test_set_path")
            or ctx.get("test_set_path")
            or Path(ctx["suite_root"]) / test_set.TEST_SET_FILE
        ),
    )
    return (
        test_set_output == inference_input
        or _compatibility_path_will_refresh(
            ctx,
            stage_name="test_set",
            input_path=inference_input,
            filename=test_set.TEST_SET_FILE,
        )
    )


def _inference_output_feeds_judge(
    ctx: dict[str, Any],
    stage_cfgs: dict[str, dict[str, Any]],
) -> bool:
    inference_cfg = stage_cfgs.get("inference")
    judge_cfg = stage_cfgs.get("judge")
    if inference_cfg is None or judge_cfg is None:
        return False
    inference_output_dir = _resolved_path(
        ctx,
        "save_dir",
        str(inference_cfg.get("save_dir") or ctx["run_root"]),
    )
    judge_input = _resolved_path(
        ctx,
        "inference_set_path",
        str(
            judge_cfg.get("inference_set_path")
            or Path(ctx["run_root"]) / INFERENCE_SET_FILE
        ),
    )
    return (inference_output_dir / INFERENCE_SET_FILE) == judge_input


def _target_tools(
    target: TargetConfig,
    profile: _CaseProfile,
    ctx: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if profile.tools:
        try:
            return build_target_tools(normalize_tool_defs(list(profile.tools))), None
        except (KeyError, TypeError, ValueError):
            return None, "Per-test-case tool schemas could not be counted."
    if target.tools is None:
        return None, None
    if target.tools.module:
        return (
            build_target_tools(
                [
                    {
                        "name": "representative_tool",
                        "description": _synthetic_text(24, "tool"),
                        "parameters": [
                            {
                                "name": "value",
                                "type": "string",
                                "description": _synthetic_text(18, "parameter"),
                            }
                        ],
                    }
                ]
            ),
            "Tool-module calls use a representative tool schema.",
        )
    if target.tools.toolset:
        toolset_path = resolve_toolset_path(
            target.tools.toolset,
            config_path=Path(ctx["config_path"]),
        )
        try:
            return build_target_tools(load_toolset_file(toolset_path)), None
        except (FileNotFoundError, OSError, ValueError):
            return (
                build_target_tools(
                    [
                        {
                            "name": "representative_tool",
                            "description": _synthetic_text(24, "tool"),
                            "parameters": [
                                {
                                    "name": "value",
                                    "type": "string",
                                    "description": _synthetic_text(18, "parameter"),
                                }
                            ],
                        }
                    ]
                ),
                "Toolset schemas could not be loaded; a representative schema was used.",
            )
    return None, None


def _transcript_xml(messages: list[tuple[str, str]]) -> str:
    parts = ["<transcript>"]
    for index, (role, content) in enumerate(messages, start=1):
        tag = "assistant" if role == "assistant" else role
        parts.append(
            f'<{tag} index="{index}">\n{escape(content)}\n</{tag}>'
        )
    parts.append("</transcript>")
    return "\n\n".join(parts)


def _project_prompt_case(
    profile: _CaseProfile,
    *,
    ctx: dict[str, Any],
    target: TargetConfig,
    max_tokens: int,
) -> tuple[StageTokenEstimate, _TranscriptProfile, list[str]]:
    estimate = StageTokenEstimate()
    notes: list[str] = []
    system_prompt = str(target.system_prompt or "").strip() or profile.system_prompt
    request_messages: list[Message] = []
    transcript_messages: list[tuple[str, str]] = []
    if system_prompt:
        request_messages.append(Message(role="system", content=system_prompt))
        transcript_messages.append(("system", system_prompt))
    request_messages.append(Message(role="user", content=profile.description))
    transcript_messages.append(("user", profile.description))

    target_output = _bounded_output(
        _PROMPT_TARGET_OUTPUT_TOKENS,
        target.model.max_tokens if isinstance(target.model, ModelConfig) else max_tokens,
    )
    target_text = _synthetic_text(target_output, "response")
    if isinstance(target.model, ModelConfig):
        tools, tool_note = _target_tools(target, profile, ctx)
        if tool_note:
            notes.append(tool_note)
        estimate.calls += 1
        estimate.input_tokens += _request_tokens(
            target.model.name,
            request_messages,
            tools=tools,
        )
        estimate.output_tokens += target_output
        if tools:
            tool_call = ToolCall(
                name=str(tools[0]["function"]["name"]),
                arguments={"query": "representative value"},
                call_id="estimated_tool_call",
            )
            follow_up = list(request_messages)
            follow_up.append(
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[tool_call],
                )
            )
            tool_result = _synthetic_text(80, "result")
            follow_up.append(
                Message(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tool_call.id,
                )
            )
            estimate.calls += 1
            estimate.input_tokens += _request_tokens(
                target.model.name,
                follow_up,
                tools=tools,
            )
            estimate.output_tokens += target_output
            if target.tools is not None and target.tools.simulator:
                simulator_prompt = (
                    inference_stage.TOOL_SIM_PROMPT
                    .replace("{{description}}", profile.description)
                    .replace("{{tool_name}}", tool_call.name)
                    .replace("{{tool_args}}", json.dumps(tool_call.arguments))
                    .replace("{{conversation}}", profile.description)
                    .replace("{{tool_history}}", "[]")
                )
                estimate.calls += 1
                estimate.input_tokens += _request_tokens(
                    target.tools.simulator,
                    simulator_prompt,
                )
                estimate.output_tokens += _SIMULATOR_OUTPUT_TOKENS
    transcript_messages.append(("assistant", target_text))
    return (
        estimate,
        _TranscriptProfile(
            kind="prompt",
            test_case_id=profile.test_case_id,
            transcript_xml=_transcript_xml(transcript_messages),
        ),
        notes,
    )


def _project_scenario_case(
    profile: _CaseProfile,
    *,
    ctx: dict[str, Any],
    target: TargetConfig,
    evaluation: EvaluationConfig,
    max_tokens: int,
) -> tuple[StageTokenEstimate, _TranscriptProfile, list[str]]:
    estimate = StageTokenEstimate()
    notes: list[str] = []
    tester = evaluation.tester
    if tester is None:
        return (
            estimate,
            _TranscriptProfile(
                "scenario",
                profile.test_case_id,
                _transcript_xml([]),
            ),
            notes,
        )
    tester_system = (
        inference_stage.TESTER_SYSTEM_PROMPT
        .replace("{{description}}", profile.description)
        .replace("{{max_turns}}", str(evaluation.inference.max_turns))
    )
    tester_messages: list[Message] = [
        Message(role="system", content=tester_system),
        Message(
            role="user",
            content="Begin the conversation now with the user's first message only.",
        ),
    ]
    target_messages: list[Message] = []
    transcript_messages: list[tuple[str, str]] = []
    system_prompt = str(target.system_prompt or "").strip() or profile.system_prompt
    if system_prompt:
        target_messages.append(Message(role="system", content=system_prompt))
        transcript_messages.append(("system", system_prompt))

    target_output = _bounded_output(
        _SCENARIO_TARGET_OUTPUT_TOKENS,
        target.model.max_tokens if isinstance(target.model, ModelConfig) else max_tokens,
    )
    tester_output = _bounded_output(
        _TESTER_OUTPUT_TOKENS,
        tester.model.max_tokens,
    )
    fixed_tools: list[dict[str, Any]] | None = None
    if isinstance(target.model, ModelConfig):
        fixed_tools, tool_note = _target_tools(target, profile, ctx)
        if tool_note:
            notes.append(tool_note)

    for turn_index in range(evaluation.inference.max_turns):
        estimate.calls += 1
        estimate.input_tokens += _request_tokens(
            tester.model.name,
            tester_messages,
        )
        estimate.output_tokens += tester_output
        user_turn = _synthetic_text(tester_output, "request")
        tester_messages.append(Message(role="assistant", content=user_turn))
        target_messages.append(Message(role="user", content=user_turn))
        transcript_messages.append(("user", user_turn))

        target_text = _synthetic_text(target_output, "response")
        if isinstance(target.model, ModelConfig):
            estimate.calls += 1
            estimate.input_tokens += _request_tokens(
                target.model.name,
                target_messages,
                tools=fixed_tools,
            )
            estimate.output_tokens += target_output
            if fixed_tools:
                estimate.calls += 1
                estimate.input_tokens += _request_tokens(
                    target.model.name,
                    target_messages
                    + [
                        Message(role="assistant", content=""),
                        Message(role="tool", content=_synthetic_text(80, "result")),
                    ],
                    tools=fixed_tools,
                )
                estimate.output_tokens += target_output
                if target.tools is not None and target.tools.simulator:
                    estimate.calls += 1
                    estimate.input_tokens += _request_tokens(
                        target.tools.simulator,
                        inference_stage.TOOL_SIM_PROMPT.replace(
                            "{{description}}",
                            profile.description,
                        ),
                    )
                    estimate.output_tokens += _SIMULATOR_OUTPUT_TOKENS
        target_messages.append(Message(role="assistant", content=target_text))
        transcript_messages.append(("assistant", target_text))
        tester_messages.append(
            Message(
                role="user",
                content=(
                    f"[Turn {turn_index + 1}/{evaluation.inference.max_turns}]\n"
                    f"<target_response>\n{target_text}\n</target_response>"
                ),
            )
        )

    return (
        estimate,
        _TranscriptProfile(
            kind="scenario",
            test_case_id=profile.test_case_id,
            transcript_xml=_transcript_xml(transcript_messages),
        ),
        notes,
    )


def _filter_case_inventory(
    inventory: _CaseInventory,
    completed_ids: set[str],
) -> _CaseInventory:
    pending = _CaseInventory()
    for kind, profiles in inventory.samples.items():
        remaining = [
            profile
            for profile in profiles
            if profile.test_case_id not in completed_ids
        ]
        if not remaining:
            continue
        pending.samples[kind] = remaining
        pending.counts[kind] = len(remaining)
    return pending


def _pending_case_inventory(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    inventory: _CaseInventory,
    *,
    upstream_changed: bool,
    forced: bool,
) -> tuple[_CaseInventory, bool]:
    if upstream_changed or forced or inventory.total == 0:
        return inventory, False

    target = ctx.get("target")
    evaluation = ctx.get("evaluation")
    if not isinstance(target, TargetConfig):
        return inventory, False
    if not isinstance(evaluation, EvaluationConfig):
        evaluation = EvaluationConfig()

    raw_test_set_path = (
        raw_cfg.get("test_set_path")
        or ctx.get("test_set_path")
        or str(Path(ctx["suite_root"]) / test_set.TEST_SET_FILE)
    )
    test_set_path = _resolved_path(
        ctx,
        "test_set_path",
        str(raw_test_set_path),
    )
    raw_output_dir = raw_cfg.get("save_dir") or str(ctx["run_root"])
    output_dir = _resolved_path(ctx, "save_dir", str(raw_output_dir))
    inference_path = output_dir / INFERENCE_SET_FILE
    if not inference_path.exists():
        return inventory, False

    resolved_max_tokens = raw_cfg.get(
        "max_tokens",
        DEFAULT_INFERENCE_MAX_TOKENS,
    )
    if not isinstance(resolved_max_tokens, int) or resolved_max_tokens <= 0:
        resolved_max_tokens = DEFAULT_INFERENCE_MAX_TOKENS
    test_set_content: bytes | None = None
    test_set_artifact_ref = (ctx.get("artifact_versions") or {}).get(
        "test_set"
    )
    rewrite_test_set = (
        not isinstance(test_set_artifact_ref, dict)
        and not inference_stage._is_versioned_test_set_artifact_path(
            test_set_path
        )
    )
    if rewrite_test_set:
        canonical_rows = normalize_test_case_rows(load_jsonl(test_set_path))
        test_set_content = (
            os.linesep.join(
                json.dumps(row, ensure_ascii=False)
                for row in canonical_rows
            )
            + os.linesep
        ).encode("utf-8")
    expected_hash = inference_stage._inference_config_fingerprint(
        target,
        evaluation,
        resolved_max_tokens,
        test_set_path=test_set_path,
        config_path=Path(ctx["config_path"]),
        test_set_content=test_set_content,
    )
    hash_path = output_dir / inference_stage._INFERENCE_CONFIG_HASH_FILE
    stored_hash = (
        hash_path.read_text(encoding="utf-8").strip()
        if hash_path.exists()
        else None
    )
    if stored_hash is not None and stored_hash != expected_hash:
        return inventory, False

    completed_ids = {
        str(row.get("test_case_id") or "")
        for row in load_jsonl(inference_path)
        if row.get("test_case_id")
    }
    return _filter_case_inventory(inventory, completed_ids), True


def _project_inventory(
    ctx: dict[str, Any],
    *,
    target: TargetConfig,
    evaluation: EvaluationConfig,
    max_tokens: int,
    inventory: _CaseInventory,
) -> tuple[StageTokenEstimate, _TranscriptInventory, list[str]]:
    aggregate = StageTokenEstimate()
    transcripts = _TranscriptInventory()
    notes: list[str] = []
    for kind, profiles in inventory.samples.items():
        total_count = inventory.counts.get(kind, 0)
        if total_count <= 0 or not profiles:
            continue
        sample_estimates: list[StageTokenEstimate] = []
        transcript_samples: list[_TranscriptProfile] = []
        for profile in _sample_evenly(profiles):
            if kind == "prompt":
                case_estimate, transcript, case_notes = _project_prompt_case(
                    profile,
                    ctx=ctx,
                    target=target,
                    max_tokens=max_tokens,
                )
            else:
                case_estimate, transcript, case_notes = _project_scenario_case(
                    profile,
                    ctx=ctx,
                    target=target,
                    evaluation=evaluation,
                    max_tokens=max_tokens,
                )
            sample_estimates.append(case_estimate)
            transcript_samples.append(transcript)
            notes.extend(case_notes)
        divisor = len(sample_estimates)
        aggregate.calls += round(
            sum(item.calls for item in sample_estimates) / divisor * total_count
        )
        aggregate.input_tokens += round(
            sum(item.input_tokens for item in sample_estimates)
            / divisor
            * total_count
        )
        aggregate.output_tokens += round(
            sum(item.output_tokens for item in sample_estimates)
            / divisor
            * total_count
        )
        transcripts.samples[kind] = transcript_samples
        transcripts.counts[kind] = total_count
    return aggregate, transcripts, notes


def _estimate_inference(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    inventory: _CaseInventory,
    *,
    upstream_changed: bool,
    forced: bool,
) -> _InferenceProjection:
    target = ctx.get("target")
    evaluation = ctx.get("evaluation")
    if not isinstance(target, TargetConfig):
        return _InferenceProjection(StageTokenEstimate(), _TranscriptInventory())
    if not isinstance(evaluation, EvaluationConfig):
        evaluation = EvaluationConfig()
    max_tokens = raw_cfg.get("max_tokens", DEFAULT_INFERENCE_MAX_TOKENS)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        max_tokens = DEFAULT_INFERENCE_MAX_TOKENS

    pending_inventory, resume_compatible = _pending_case_inventory(
        ctx,
        raw_cfg,
        inventory,
        upstream_changed=upstream_changed,
        forced=forced,
    )
    aggregate, pending_transcripts, pending_notes = _project_inventory(
        ctx,
        target=target,
        evaluation=evaluation,
        max_tokens=max_tokens,
        inventory=pending_inventory,
    )
    _full_estimate, full_transcripts, full_notes = _project_inventory(
        ctx,
        target=target,
        evaluation=evaluation,
        max_tokens=max_tokens,
        inventory=inventory,
    )
    transcripts = full_transcripts
    if resume_compatible and pending_inventory.total < inventory.total:
        raw_output_dir = raw_cfg.get("save_dir") or str(ctx["run_root"])
        output_dir = _resolved_path(ctx, "save_dir", str(raw_output_dir))
        actual_transcripts = _actual_transcripts(
            output_dir / INFERENCE_SET_FILE
        )
        transcripts = _merge_transcript_inventories(
            actual_transcripts or _TranscriptInventory(),
            pending_transcripts,
        )
    notes = pending_notes + full_notes

    if not isinstance(target.model, ModelConfig) and inventory.total:
        target_kind = (
            "callable"
            if target.callable
            else "connector"
            if target.connector
            else "endpoint"
            if target.endpoint
            else "sandbox"
        )
        notes.append(
            f"Target-internal usage for the {target_kind} target is not included."
        )
    return _InferenceProjection(
        estimate=aggregate,
        transcripts=transcripts,
        pending_cases=pending_inventory.total,
        notes=list(dict.fromkeys(notes)),
    )


def _merge_transcript_inventories(
    *inventories: _TranscriptInventory,
) -> _TranscriptInventory:
    merged = _TranscriptInventory()
    kinds = {
        kind
        for inventory in inventories
        for kind in inventory.samples
    }
    for kind in kinds:
        components = [
            (
                inventory.samples[kind],
                inventory.counts.get(
                    kind,
                    len(inventory.samples[kind]),
                ),
            )
            for inventory in inventories
            if inventory.samples.get(kind)
            and inventory.counts.get(kind, 0) > 0
        ]
        total_count = sum(count for _profiles, count in components)
        if total_count <= 0:
            continue
        sample_budget = min(_MAX_PROFILE_SAMPLES, total_count)
        allocations = [
            min(
                count,
                max(1, int(sample_budget * count / total_count)),
            )
            for _profiles, count in components
        ]
        while sum(allocations) < sample_budget:
            index = max(
                range(len(components)),
                key=lambda item: components[item][1] - allocations[item],
            )
            if allocations[index] >= components[index][1]:
                break
            allocations[index] += 1
        while sum(allocations) > sample_budget:
            index = max(
                (
                    item
                    for item in range(len(components))
                    if allocations[item] > 1
                ),
                key=lambda item: allocations[item],
            )
            allocations[index] -= 1

        samples: list[_TranscriptProfile] = []
        for (profiles, _count), allocation in zip(
            components,
            allocations,
            strict=True,
        ):
            selected = _sample_evenly(
                profiles,
                limit=min(allocation, len(profiles)),
            )
            samples.extend(
                selected[index % len(selected)]
                for index in range(allocation)
            )
        merged.samples[kind] = samples
        merged.counts[kind] = total_count
    return merged


def _actual_transcripts(
    inference_path: Path,
) -> _TranscriptInventory | None:
    rows = load_jsonl(inference_path)
    if not rows:
        return None
    grouped: dict[str, list[_TranscriptProfile]] = {
        "prompt": [],
        "scenario": [],
    }
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("stop_reason") in _UNSCORABLE_STOP_REASONS:
            continue
        kind = str(row.get("type") or "prompt")
        if kind not in grouped:
            kind = "prompt"
        transcript = Transcript(
            metadata=TranscriptMetadata(
                kind=kind,
                test_case_id=str(row.get("test_case_id") or ""),
                behavior=str(row.get("behavior") or ""),
                target=str(row.get("target") or ""),
                tester_model=str(row.get("tester_model") or ""),
                dimensions=row_factors(row),
            ),
            events=[
                TranscriptEvent.model_validate(event)
                for event in row.get("events", [])
                if isinstance(event, dict)
            ],
            stop_reason=row.get("stop_reason"),
        )
        transcript_xml, _ = transcript.format_transcript_xml(
            "target",
            skip_system=False,
        )
        counts[kind] = counts.get(kind, 0) + 1
        grouped[kind].append(
            _TranscriptProfile(
                kind=kind,
                test_case_id=str(row.get("test_case_id") or ""),
                transcript_xml=transcript_xml,
            )
        )
    return _TranscriptInventory(
        samples={
            kind: profiles
            for kind, profiles in grouped.items()
            if profiles
        },
        counts=counts,
    )


def _actual_transcripts_for_judge(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
) -> _TranscriptInventory | None:
    raw_path = (
        raw_cfg.get("inference_set_path")
        or str(Path(ctx["run_root"]) / INFERENCE_SET_FILE)
    )
    return _actual_transcripts(
        _resolved_path(ctx, "inference_set_path", raw_path)
    )


def _synthetic_judge_output(
    score_keys: list[str],
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    categories = [
        item
        for item in taxonomy.get("behavior_categories", [])
        if isinstance(item, dict)
    ]
    return {
        "dimensions": {key: False for key in score_keys},
        "justification": _synthetic_text(36, "justification"),
        "dimension_justifications": {
            key: _synthetic_text(28, "evidence")
            for key in score_keys
        },
        NODE_JUDGMENTS_KEY: [
            {
                "node_name": str(item.get("name") or "behavior"),
                "violated": False,
                "confidence": "high",
                "reasoning": _synthetic_text(24, "reasoning"),
            }
            for item in categories
        ],
        "highlights": "<cite ref=\"1\" />",
        "narrative": _synthetic_text(72, "narrative"),
    }


def _filter_transcript_inventory(
    transcripts: _TranscriptInventory,
    completed_keys: set[tuple[str, str]],
) -> _TranscriptInventory:
    pending = _TranscriptInventory()
    for kind, profiles in transcripts.samples.items():
        remaining = [
            profile
            for profile in profiles
            if (kind, profile.test_case_id) not in completed_keys
        ]
        if not remaining:
            continue
        pending.samples[kind] = remaining
        pending.counts[kind] = len(remaining)
    return pending


def _pending_judge_transcripts(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    *,
    taxonomy: dict[str, Any],
    judge_cfg: Any,
    contract: dict[str, Any],
    transcripts: _TranscriptInventory,
    upstream_changed: bool,
    forced: bool,
) -> _TranscriptInventory:
    if upstream_changed or forced:
        return transcripts

    raw_inference_path = (
        raw_cfg.get("inference_set_path")
        or str(Path(ctx["run_root"]) / INFERENCE_SET_FILE)
    )
    inference_path = _resolved_path(
        ctx,
        "inference_set_path",
        str(raw_inference_path),
    )
    raw_output_dir = raw_cfg.get("save_dir") or str(ctx["run_root"])
    output_dir = _resolved_path(ctx, "save_dir", str(raw_output_dir))
    scores_path = output_dir / SCORES_FILE
    if not inference_path.exists() or not scores_path.exists():
        return transcripts

    expected_hash = judge_stage._judge_config_fingerprint(
        judge_model=judge_cfg.model.name,
        judge_temperature=judge_cfg.model.temperature,
        judge_max_tokens=judge_cfg.model.max_tokens,
        judge_reasoning_effort=judge_cfg.model.reasoning_effort,
        judge_n=judge_cfg.n,
        judge_dimensions=judge_cfg.dimensions,
        disabled_dimensions=judge_cfg.disabled_dimensions,
        policy_raw=taxonomy,
        system_prompt=str(contract["system_prompt"]),
        inference_set_path=inference_path,
    )
    hash_path = output_dir / judge_stage._JUDGE_CONFIG_HASH_FILE
    stored_hash = (
        hash_path.read_text(encoding="utf-8").strip()
        if hash_path.exists()
        else None
    )
    if stored_hash is not None and stored_hash != expected_hash:
        return transcripts

    completed_keys = {
        (
            str(row.get("type") or ""),
            str(row.get("test_case_id") or ""),
        )
        for row in load_jsonl(scores_path)
        if row.get("test_case_id")
    }
    return _filter_transcript_inventory(transcripts, completed_keys)


def _estimate_judge(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    taxonomy: dict[str, Any],
    projected_transcripts: _TranscriptInventory | None,
    *,
    upstream_changed: bool,
    forced: bool,
) -> StageTokenEstimate:
    evaluation = ctx.get("evaluation")
    if (
        not isinstance(evaluation, EvaluationConfig)
        or evaluation.judge is None
    ):
        return StageTokenEstimate()
    judge_cfg = evaluation.judge
    contract = build_judge_contract(
        template=judge_stage.JUDGE_SYSTEM_PROMPT,
        policy_raw=taxonomy,
        judge_dimensions=judge_cfg.dimensions,
        disabled_dimensions=judge_cfg.disabled_dimensions,
        schema_name="transcript_judgment",
    )
    actual_transcripts = _actual_transcripts_for_judge(ctx, raw_cfg)
    if upstream_changed:
        transcripts = projected_transcripts or _TranscriptInventory()
    else:
        transcripts = (
            actual_transcripts
            or projected_transcripts
            or _TranscriptInventory()
        )
    transcripts = _pending_judge_transcripts(
        ctx,
        raw_cfg,
        taxonomy=taxonomy,
        judge_cfg=judge_cfg,
        contract=contract,
        transcripts=transcripts,
        upstream_changed=upstream_changed,
        forced=forced,
    )
    per_call_output = _bounded_output(
        estimate_token_count(
            judge_cfg.model.name,
            text=json.dumps(
                _synthetic_judge_output(
                    contract["score_keys"],
                    taxonomy,
                ),
                ensure_ascii=False,
            ),
        ),
        judge_cfg.model.max_tokens or DEFAULT_JUDGE_MAX_TOKENS,
    )

    estimate = StageTokenEstimate()
    for kind, profiles in transcripts.samples.items():
        count = transcripts.counts.get(kind, 0)
        if not profiles or count <= 0:
            continue
        samples = _sample_evenly(profiles)
        per_row_input = _scaled_sum(
            samples,
            count,
            lambda profile: _request_tokens(
                judge_cfg.model.name,
                [
                    Message(
                        role="system",
                        content=contract["system_prompt"],
                    ),
                    Message(
                        role="user",
                        content=f"# Transcript\n{profile.transcript_xml}",
                    ),
                ],
                response_schema=contract["response_schema"]["json_schema"],
            ),
        )
        estimate.calls += count * judge_cfg.n
        estimate.input_tokens += per_row_input * judge_cfg.n
        estimate.output_tokens += count * judge_cfg.n * per_call_output
    return estimate


def estimate_pipeline_tokens(
    ctx: dict[str, Any],
    stages_to_run: list[tuple[str, Any, dict[str, Any]]],
    *,
    forced_stages: set[str] | None = None,
) -> PipelineTokenEstimate:
    """Estimate usage for the uncached stages selected by the runner."""
    result = PipelineTokenEstimate()
    stage_cfgs = {
        name: raw_cfg
        for name, _module, raw_cfg in stages_to_run
    }
    if not stage_cfgs:
        return result

    taxonomies: dict[str, dict[str, Any]] = {}
    synthetic_taxonomy_stages: list[str] = []
    for taxonomy_stage in ("test_set", "judge"):
        if taxonomy_stage not in stage_cfgs:
            continue
        taxonomy, is_synthetic = _taxonomy_for_stage(
            ctx,
            stage_cfgs,
            taxonomy_stage,
        )
        taxonomies[taxonomy_stage] = taxonomy
        if is_synthetic:
            synthetic_taxonomy_stages.append(taxonomy_stage)
    if synthetic_taxonomy_stages:
        result.notes.append(
            "Taxonomy-dependent stages use a representative generated taxonomy."
        )
    test_set_changes_inference = _test_set_output_feeds_inference(
        ctx,
        stage_cfgs,
    )
    cases = _case_inventory(
        ctx,
        stage_cfgs,
        prefer_generated=test_set_changes_inference,
    )
    projected_transcripts: _TranscriptInventory | None = None
    inference_pending_cases = 0
    forced = forced_stages or set()

    for stage_name, _module, raw_cfg in stages_to_run:
        try:
            if stage_name == "systematize":
                estimate = _estimate_systematize(ctx, raw_cfg)
                if raw_cfg.get("web_search", True):
                    result.notes.append(
                        "Provider-added web-search context is not included."
                    )
            elif stage_name == "test_set":
                estimate = _estimate_test_set(
                    ctx,
                    raw_cfg,
                    taxonomies["test_set"],
                )
            elif stage_name == "inference":
                projection = _estimate_inference(
                    ctx,
                    raw_cfg,
                    cases,
                    upstream_changed=test_set_changes_inference,
                    forced=stage_name in forced,
                )
                estimate = projection.estimate
                projected_transcripts = projection.transcripts
                inference_pending_cases = projection.pending_cases
                result.notes.extend(projection.notes)
            elif stage_name == "judge":
                estimate = _estimate_judge(
                    ctx,
                    raw_cfg,
                    taxonomies["judge"],
                    projected_transcripts,
                    upstream_changed=(
                        (
                            inference_pending_cases > 0
                            or "inference" in forced
                        )
                        and _inference_output_feeds_judge(
                            ctx,
                            stage_cfgs,
                        )
                    ),
                    forced=stage_name in forced,
                )
            else:
                continue
        except (KeyError, TypeError, ValueError, OSError) as exc:
            result.notes.append(
                f"{stage_name} estimate unavailable: {exc}"
            )
            continue
        if estimate.calls or estimate.total_tokens:
            result.stages[stage_name] = estimate

    result.notes.append("Retries and provider-side hidden overhead are not included.")
    result.notes = list(dict.fromkeys(result.notes))
    return result
