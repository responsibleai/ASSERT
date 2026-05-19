"""Stratification: dimension normalization, generation, and catalog rendering."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from p2m.config import parse_model_config, resolve_stage_paths
from p2m.core.io import (
    STRATIFICATION_FILE,
    stratification_dimensions,
    fill_template,
    get_permissible_flag,
    load_policy,
    load_prompt_text,
    normalize_test_case_context,
    resolve_path,
    write_json,
)
from p2m.core.model_client import GenerateOptions, generate_structured

DEFAULT_LEVEL_COUNT = 3

STRATIFICATION_PROMPT_TEMPLATE = load_prompt_text("test_set_stratification.md")

SCOPE = "suite"
SUITE_OUTPUT = STRATIFICATION_FILE


def render_behavior_categories(taxonomy: dict[str, Any]) -> str:
    lines = []
    for behavior in taxonomy.get("behavior_categories", []):
        permissible = get_permissible_flag(behavior, default=True)
        status = "PERMISSIBLE" if permissible else "NOT PERMISSIBLE"
        lines.append(
            f"- {behavior['name']} ({status}): "
            f"{str(behavior.get('definition') or '').strip()}"
        )
    return "\n".join(lines)


def build_behavior_factor(taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for behavior in taxonomy.get("behavior_categories", []):
        name = str(behavior.get("name") or "").strip()
        definition = str(behavior.get("definition") or "").strip()
        if not name or not definition:
            raise ValueError(
                "taxonomy behavior_categories must include non-empty name and definition"
            )
        if name in seen_names:
            raise ValueError(f"duplicate taxonomy behavior name: {name}")
        seen_names.add(name)
        levels.append({"name": name, "description": definition})
    if not levels:
        raise ValueError("taxonomy must contain at least one behavior")
    return levels


def render_factors_section(dimensions: list[dict[str, str]]) -> str:
    return "\n".join(
        f"  - `{dimension['name']}`: {dimension['description']}" for dimension in dimensions
    )


def _normalize_factor_levels(
    factor_name: str,
    entries: Any,
    *,
    preserve_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{factor_name} must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(
                f"{factor_name} entries must be objects with name and definition"
            )
        name = str(entry.get("name") or "").strip()
        definition = str(entry.get("definition") or "").strip()
        if not name or not definition:
            raise ValueError(
                f"{factor_name} entries require non-empty name and definition"
            )
        if name in seen_names:
            raise ValueError(f"{factor_name} contains duplicate name: {name}")
        seen_names.add(name)
        item: dict[str, Any] = {"name": name, "definition": definition}
        for field in preserve_fields:
            if field in entry and entry[field] is not None:
                item[field] = entry[field]
        normalized.append(item)
    return normalized


def _normalize_behavior_levels(entries: Any) -> list[dict[str, str]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("behavior must be a non-empty list")
    normalized: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(
                "behavior entries must be objects with name and description"
            )
        name = str(entry.get("name") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not name or not description:
            raise ValueError(
                "behavior entries require non-empty name and description"
            )
        if name in seen_names:
            raise ValueError(f"behavior contains duplicate name: {name}")
        seen_names.add(name)
        normalized.append({"name": name, "description": description})
    return normalized


def normalize_stratification(
    raw_stratification: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    factor_order: list[str] | None = None,
    inject_behavior: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Validate and normalize a stratification."""
    if not isinstance(raw_stratification, dict):
        raise ValueError("stratification must be a JSON object")

    metadata = {
        key: value for key, value in raw_stratification.items() if key.startswith("_")
    }
    raw_factors = {
        key: value
        for key, value in raw_stratification.items()
        if not key.startswith("_") and key != "behavior"
    }
    raw_behavior = raw_stratification.get("behavior")

    if factor_order is not None:
        expected = list(factor_order)
        actual = list(raw_factors)
        if set(actual) != set(expected):
            raise ValueError(
                "stratification dimensions must match the configured dimension list exactly"
            )
    else:
        expected = list(raw_factors)

    normalized: dict[str, Any] = {}

    behavior_entries = build_behavior_factor(taxonomy)
    if raw_behavior is not None:
        normalized["behavior"] = _normalize_behavior_levels(raw_behavior)
    elif inject_behavior:
        normalized["behavior"] = behavior_entries

    for factor_name in expected:
        if factor_name.startswith("_"):
            raise ValueError(
                f"stratification dimension names must not start with '_': {factor_name}"
            )
        if factor_name == "behavior":
            raise ValueError("behavior is reserved for taxonomy behavior_categories")
        if factor_name not in raw_factors:
            raise ValueError(f"missing stratification dimension: {factor_name}")
        normalized[factor_name] = _normalize_factor_levels(
            factor_name, raw_factors[factor_name]
        )

    normalized.update(metadata)
    return normalized


def render_stratification_catalog(
    stratification: dict[str, list[dict[str, Any]]],
    *,
    include_behavior: bool = True,
) -> str:
    dimensions = [
        key
        for key in stratification
        if not key.startswith("_")
        and (include_behavior or key != "behavior")
    ]
    blocks = []
    for factor_name in dimensions:
        title = factor_name.replace("_", " ").capitalize()
        text_field = "description" if factor_name == "behavior" else "definition"
        body = "\n".join(
            f"- {entry['name']}: {entry[text_field]}"
            for entry in stratification[factor_name]
        )
        blocks.append(f"{title}:\n{body}")
    return "\n\n".join(blocks)


def _stratification_response_schema(
    level_count: int,
    *,
    dimensions: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            factor_name: {
                "type": "array",
                "minItems": level_count,
                "maxItems": level_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "definition": {"type": "string"},
                    },
                    "required": ["name", "definition"],
                },
            }
            for factor_name in dimensions
        },
        "required": list(dimensions),
    }


async def run_stratification(
    *,
    taxonomy_path: str,
    out_dir: str,
    dimensions: list[dict] | None = None,
    context: str | None = None,
    model: str | None = None,
    level_count: int = DEFAULT_LEVEL_COUNT,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    if level_count <= 0:
        raise ValueError("level_count must be > 0")
    taxonomy = load_policy(taxonomy_path)
    output_dir = resolve_path(out_dir)
    normalized_context = normalize_test_case_context(context) if context else None
    raw_factors = [] if dimensions is None else dimensions
    if not isinstance(raw_factors, list):
        raise ValueError("dimensions must be a list when provided")

    normalized_factors: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, dimension in enumerate(raw_factors, start=1):
        if not isinstance(dimension, dict):
            raise ValueError(f"dimensions[{index}] must be a mapping")
        name = str(dimension.get("name") or "").strip()
        description = str(dimension.get("description") or "").strip()
        raw_levels = dimension.get("levels")
        if not name:
            raise ValueError(f"dimensions[{index}]: name is required")
        if isinstance(raw_levels, list) and len(raw_levels) == 0:
            raise ValueError(f"dimensions[{index}] '{name}': levels must not be empty")
        has_levels = isinstance(raw_levels, list) and len(raw_levels) > 0
        if not description and not has_levels:
            raise ValueError(
                f"dimensions[{index}] '{name}': description is required when levels are not provided"
            )
        if name.startswith("_"):
            raise ValueError(f"dimension names must not start with '_': {name}")
        if name == "behavior":
            raise ValueError("behavior is reserved and cannot appear in dimensions")
        if name in seen_names:
            raise ValueError(f"duplicate dimension name: {name}")
        seen_names.add(name)
        normalized_factor: dict[str, Any] = {"name": name}
        if description:
            normalized_factor["description"] = description
        if raw_levels is not None:
            if not isinstance(raw_levels, list):
                raise ValueError(f"dimensions[{index}].levels must be a list")
            if not raw_levels:
                raise ValueError(f"dimensions[{index}] '{name}': levels must not be empty")
            normalized_factor["levels"] = _normalize_factor_levels(name, raw_levels)
        normalized_factors.append(normalized_factor)

    factor_names = [dimension["name"] for dimension in normalized_factors]
    if not factor_names:
        stratification = normalize_stratification({}, taxonomy, inject_behavior=True)
    else:
        provided_stratification = {
            dimension["name"]: dimension["levels"]
            for dimension in normalized_factors
            if dimension.get("levels")
        }
        factors_to_generate = [
            {"name": dimension["name"], "description": dimension["description"]}
            for dimension in normalized_factors
            if not dimension.get("levels")
        ]
        if not factors_to_generate:
            stratification = normalize_stratification(
                provided_stratification,
                taxonomy,
                factor_order=factor_names,
                inject_behavior=True,
            )
        else:
            if not model:
                raise ValueError(
                    "stratification.model is required when any stratification dimension is missing levels"
                )
            if reasoning_effort is not None:
                temperature = None
            prompt = fill_template(
                STRATIFICATION_PROMPT_TEMPLATE,
                {
                    "behavior_name": str(taxonomy.get("behavior", {}).get("name") or "behavior"),
                    "behavior_categories": render_behavior_categories(taxonomy),
                    "context": normalized_context or "- (no additional context provided)",
                    "factors_section": render_factors_section(factors_to_generate),
                },
            )
            response = await generate_structured(
                model,
                prompt,
                schema_name="policy_stratification",
                json_schema=_stratification_response_schema(
                    level_count,
                    dimensions=tuple(dimension["name"] for dimension in factors_to_generate),
                ),
                options=GenerateOptions(
                    temperature=temperature,
                    max_tokens=50_000,
                    web_search=True,
                    reasoning_effort=reasoning_effort,
                ),
            )
            parsed = response.parsed
            if not isinstance(parsed, dict):
                raise ValueError("stratification generation returned invalid payload")
            stratification = normalize_stratification(
                {**provided_stratification, **parsed},
                taxonomy,
                factor_order=factor_names,
                inject_behavior=True,
            )

    if normalized_context:
        stratification["_context"] = normalized_context

    stratification_path = output_dir / STRATIFICATION_FILE
    write_json(stratification_path, stratification)

    factor_sizes = {name: len(stratification[name]) for name in stratification_dimensions(stratification)}

    return {
        "stratification_path": str(stratification_path),
        "factor_sizes": factor_sizes,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and generate the stratification."""
    level_count = raw_cfg.get("level_count", DEFAULT_LEVEL_COUNT)
    if not isinstance(level_count, int) or level_count <= 0:
        raise ValueError("stratification.level_count must be a positive integer")

    dimensions = ctx.get("dimensions")
    context = ctx.get("context")
    if context is not None and not isinstance(context, str):
        raise ValueError("context must be a string when provided")

    model_cfg = None
    model_raw = raw_cfg.get("model")
    if model_raw is not None:
        model_cfg = parse_model_config(model_raw, field_name="stratification.model")
    if any(
        isinstance(dimension, dict)
        and not dimension.get("levels")
        for dimension in (dimensions or [])
    ) and model_cfg is None:
        raise ValueError(
            "stratification.model is required when any stratification dimension is missing levels"
        )

    suite_root = Path(ctx["suite_root"])
    cfg = resolve_stage_paths(
        {
            "taxonomy_path": raw_cfg.get("taxonomy_path") or ctx.get("taxonomy_path") or str(suite_root / "taxonomy.json"),
            "save_dir": raw_cfg.get("save_dir") or ctx.get("stratification_artifact_dir") or str(suite_root),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )

    result = await run_stratification(
        taxonomy_path=cfg["taxonomy_path"],
        out_dir=cfg["save_dir"],
        dimensions=dimensions,
        context=context,
        model=model_cfg.name if model_cfg is not None else None,
        level_count=level_count,
        reasoning_effort=model_cfg.reasoning_effort if model_cfg is not None else None,
        temperature=model_cfg.temperature if model_cfg is not None else None,
    )
    log.debug(f"stratification: factor_sizes={result.get('factor_sizes', {})}")
    return {
        "stratification_path": result["stratification_path"],
        "_summary": {
            "factor_sizes": result.get("factor_sizes", {}),
        },
    }
