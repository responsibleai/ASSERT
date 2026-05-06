"""Design: factor normalization, generation, and catalog rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from p2m.config import parse_model_config, resolve_stage_paths
from p2m.core.io import (
    DESIGN_FILE,
    design_factors,
    fill_template,
    get_permissible_flag,
    load_taxonomy,
    load_prompt_text,
    normalize_seed_context,
    resolve_path,
    write_json,
)
from p2m.core.model_client import GenerateOptions, generate_structured

DEFAULT_LEVEL_COUNT = 3

DESIGN_PROMPT_TEMPLATE = load_prompt_text("seeds_design.md")

SCOPE = "suite"
SUITE_OUTPUT = DESIGN_FILE


def render_failure_modes(taxonomy: dict[str, Any]) -> str:
    lines = []
    for failure_mode in taxonomy.get("failure_modes", []):
        permissible = get_permissible_flag(failure_mode, default=True)
        status = "PERMISSIBLE" if permissible else "NOT PERMISSIBLE"
        lines.append(
            f"- {failure_mode['name']} ({status}): "
            f"{str(failure_mode.get('definition') or '').strip()}"
        )
    return "\n".join(lines)


def build_failure_mode_factor(taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for failure_mode in taxonomy.get("failure_modes", []):
        name = str(failure_mode.get("name") or "").strip()
        definition = str(failure_mode.get("definition") or "").strip()
        if not name or not definition:
            raise ValueError(
                "taxonomy failure_modes must include non-empty name and definition"
            )
        if name in seen_names:
            raise ValueError(f"duplicate taxonomy failure_mode name: {name}")
        seen_names.add(name)
        levels.append({"name": name, "description": definition})
    if not levels:
        raise ValueError("taxonomy must contain at least one failure_mode")
    return levels


def render_factors_section(factors: list[dict[str, str]]) -> str:
    return "\n".join(
        f"  - `{factor['name']}`: {factor['description']}" for factor in factors
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


def _normalize_failure_mode_levels(entries: Any) -> list[dict[str, str]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("failure_mode must be a non-empty list")
    normalized: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(
                "failure_mode entries must be objects with name and description"
            )
        name = str(entry.get("name") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not name or not description:
            raise ValueError(
                "failure_mode entries require non-empty name and description"
            )
        if name in seen_names:
            raise ValueError(f"failure_mode contains duplicate name: {name}")
        seen_names.add(name)
        normalized.append({"name": name, "description": description})
    return normalized


def normalize_design(
    raw_design: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    factor_order: list[str] | None = None,
    inject_failure_mode: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Validate and normalize a design."""
    if not isinstance(raw_design, dict):
        raise ValueError("design must be a JSON object")

    metadata = {
        key: value for key, value in raw_design.items() if key.startswith("_")
    }
    raw_factors = {
        key: value
        for key, value in raw_design.items()
        if not key.startswith("_") and key != "failure_mode"
    }
    raw_failure_mode = raw_design.get("failure_mode")

    if factor_order is not None:
        expected = list(factor_order)
        actual = list(raw_factors)
        if set(actual) != set(expected):
            raise ValueError(
                "design factors must match the configured factor list exactly"
            )
    else:
        expected = list(raw_factors)

    normalized: dict[str, Any] = {}

    failure_mode_entries = build_failure_mode_factor(taxonomy)
    if raw_failure_mode is not None:
        normalized["failure_mode"] = _normalize_failure_mode_levels(raw_failure_mode)
    elif inject_failure_mode:
        normalized["failure_mode"] = failure_mode_entries

    for factor_name in expected:
        if factor_name.startswith("_"):
            raise ValueError(
                f"design factor names must not start with '_': {factor_name}"
            )
        if factor_name == "failure_mode":
            raise ValueError("failure_mode is reserved for taxonomy failure_modes")
        if factor_name not in raw_factors:
            raise ValueError(f"missing design factor: {factor_name}")
        normalized[factor_name] = _normalize_factor_levels(
            factor_name, raw_factors[factor_name]
        )

    normalized.update(metadata)
    return normalized


def render_design_catalog(
    design: dict[str, list[dict[str, Any]]],
    *,
    include_failure_mode: bool = True,
) -> str:
    factors = [
        key
        for key in design
        if not key.startswith("_")
        and (include_failure_mode or key != "failure_mode")
    ]
    blocks = []
    for factor_name in factors:
        title = factor_name.replace("_", " ").capitalize()
        text_field = "description" if factor_name == "failure_mode" else "definition"
        body = "\n".join(
            f"- {entry['name']}: {entry[text_field]}"
            for entry in design[factor_name]
        )
        blocks.append(f"{title}:\n{body}")
    return "\n\n".join(blocks)


def _design_response_schema(
    level_count: int,
    *,
    factors: tuple[str, ...],
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
            for factor_name in factors
        },
        "required": list(factors),
    }


async def run_design(
    *,
    taxonomy_path: str,
    out_dir: str,
    factors: list[dict] | None = None,
    context: str | None = None,
    model: str | None = None,
    level_count: int = DEFAULT_LEVEL_COUNT,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    if level_count <= 0:
        raise ValueError("level_count must be > 0")
    taxonomy = load_taxonomy(taxonomy_path)
    output_dir = resolve_path(out_dir)
    normalized_context = normalize_seed_context(context) if context else None
    raw_factors = [] if factors is None else factors
    if not isinstance(raw_factors, list):
        raise ValueError("factors must be a list when provided")

    normalized_factors: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, factor in enumerate(raw_factors, start=1):
        if not isinstance(factor, dict):
            raise ValueError(f"factors[{index}] must be a mapping")
        name = str(factor.get("name") or "").strip()
        description = str(factor.get("description") or "").strip()
        raw_levels = factor.get("levels")
        if not name:
            raise ValueError(f"factors[{index}]: name is required")
        if isinstance(raw_levels, list) and len(raw_levels) == 0:
            raise ValueError(f"factors[{index}] '{name}': levels must not be empty")
        has_levels = isinstance(raw_levels, list) and len(raw_levels) > 0
        if not description and not has_levels:
            raise ValueError(
                f"factors[{index}] '{name}': description is required when levels are not provided"
            )
        if name.startswith("_"):
            raise ValueError(f"factor names must not start with '_': {name}")
        if name == "failure_mode":
            raise ValueError("failure_mode is reserved and cannot appear in factors")
        if name in seen_names:
            raise ValueError(f"duplicate factor name: {name}")
        seen_names.add(name)
        normalized_factor: dict[str, Any] = {"name": name}
        if description:
            normalized_factor["description"] = description
        if raw_levels is not None:
            if not isinstance(raw_levels, list):
                raise ValueError(f"factors[{index}].levels must be a list")
            if not raw_levels:
                raise ValueError(f"factors[{index}] '{name}': levels must not be empty")
            normalized_factor["levels"] = _normalize_factor_levels(name, raw_levels)
        normalized_factors.append(normalized_factor)

    factor_names = [factor["name"] for factor in normalized_factors]
    if not factor_names:
        design = normalize_design({}, taxonomy, inject_failure_mode=True)
    else:
        provided_design = {
            factor["name"]: factor["levels"]
            for factor in normalized_factors
            if factor.get("levels")
        }
        factors_to_generate = [
            {"name": factor["name"], "description": factor["description"]}
            for factor in normalized_factors
            if not factor.get("levels")
        ]
        if not factors_to_generate:
            design = normalize_design(
                provided_design,
                taxonomy,
                factor_order=factor_names,
                inject_failure_mode=True,
            )
        else:
            if not model:
                raise ValueError(
                    "design.model is required when any design factor is missing levels"
                )
            if reasoning_effort is not None:
                temperature = None
            prompt = fill_template(
                DESIGN_PROMPT_TEMPLATE,
                {
                    "spec_name": str(taxonomy.get("spec", {}).get("name") or "spec"),
                    "failure_modes": render_failure_modes(taxonomy),
                    "context": normalized_context or "- (no additional context provided)",
                    "factors_section": render_factors_section(factors_to_generate),
                },
            )
            response = await generate_structured(
                model,
                prompt,
                schema_name="taxonomy_design",
                json_schema=_design_response_schema(
                    level_count,
                    factors=tuple(factor["name"] for factor in factors_to_generate),
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
                raise ValueError("design generation returned invalid payload")
            design = normalize_design(
                {**provided_design, **parsed},
                taxonomy,
                factor_order=factor_names,
                inject_failure_mode=True,
            )

    if normalized_context:
        design["_context"] = normalized_context

    design_path = output_dir / DESIGN_FILE
    write_json(design_path, design)

    factor_sizes = {name: len(design[name]) for name in design_factors(design)}

    return {
        "design_path": str(design_path),
        "factor_sizes": factor_sizes,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and generate the design."""
    level_count = raw_cfg.get("level_count", DEFAULT_LEVEL_COUNT)
    if not isinstance(level_count, int) or level_count <= 0:
        raise ValueError("design.level_count must be a positive integer")

    factors = ctx.get("factors")
    context = ctx.get("context")
    if context is not None and not isinstance(context, str):
        raise ValueError("context must be a string when provided")

    model_cfg = None
    model_raw = raw_cfg.get("model")
    if model_raw is not None:
        model_cfg = parse_model_config(model_raw, field_name="design.model")
    if any(
        isinstance(factor, dict)
        and not factor.get("levels")
        for factor in (factors or [])
    ) and model_cfg is None:
        raise ValueError(
            "design.model is required when any design factor is missing levels"
        )

    suite_root = Path(ctx["suite_root"])
    cfg = resolve_stage_paths(
        {
            "taxonomy_path": raw_cfg.get("taxonomy_path") or str(suite_root / "taxonomy.json"),
            "save_dir": raw_cfg.get("save_dir") or str(suite_root),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )

    result = await run_design(
        taxonomy_path=cfg["taxonomy_path"],
        out_dir=cfg["save_dir"],
        factors=factors,
        context=context,
        model=model_cfg.name if model_cfg is not None else None,
        level_count=level_count,
        reasoning_effort=model_cfg.reasoning_effort if model_cfg is not None else None,
        temperature=model_cfg.temperature if model_cfg is not None else None,
    )
    return {
        "design_path": result["design_path"],
        "_summary": {
            "factor_sizes": result.get("factor_sizes", {}),
        },
    }
