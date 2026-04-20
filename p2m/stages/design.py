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
    load_policy,
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


def render_behaviors(policy: dict[str, Any]) -> str:
    lines = []
    for behavior in policy.get("behaviors", []):
        permissible = get_permissible_flag(behavior, default=True)
        status = "PERMISSIBLE" if permissible else "NOT PERMISSIBLE"
        lines.append(
            f"- {behavior['name']} ({status}): "
            f"{str(behavior.get('definition') or '').strip()}"
        )
    return "\n".join(lines)


def build_behavior_factor(policy: dict[str, Any]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for behavior in policy.get("behaviors", []):
        name = str(behavior.get("name") or "").strip()
        definition = str(behavior.get("definition") or "").strip()
        if not name or not definition:
            raise ValueError(
                "policy behaviors must include non-empty name and definition"
            )
        if name in seen_names:
            raise ValueError(f"duplicate policy behavior name: {name}")
        seen_names.add(name)
        levels.append({"name": name, "description": definition})
    if not levels:
        raise ValueError("policy must contain at least one behavior")
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


def normalize_design(
    raw_design: dict[str, Any],
    policy: dict[str, Any],
    *,
    factor_order: list[str] | None = None,
    inject_behavior: bool = False,
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
        if not key.startswith("_") and key != "behavior"
    }
    raw_behavior = raw_design.get("behavior")

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

    behavior_entries = build_behavior_factor(policy)
    if raw_behavior is not None:
        normalized["behavior"] = _normalize_behavior_levels(raw_behavior)
    elif inject_behavior:
        normalized["behavior"] = behavior_entries

    for factor_name in expected:
        if factor_name.startswith("_"):
            raise ValueError(
                f"design factor names must not start with '_': {factor_name}"
            )
        if factor_name == "behavior":
            raise ValueError("behavior is reserved for policy behaviors")
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
    include_behavior: bool = True,
) -> str:
    factors = [
        key
        for key in design
        if not key.startswith("_")
        and (include_behavior or key != "behavior")
    ]
    blocks = []
    for factor_name in factors:
        title = factor_name.replace("_", " ").capitalize()
        text_field = "description" if factor_name == "behavior" else "definition"
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
    policy_path: str,
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
    policy = load_policy(policy_path)
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
        if name == "behavior":
            raise ValueError("behavior is reserved and cannot appear in factors")
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
        design = normalize_design({}, policy, inject_behavior=True)
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
                policy,
                factor_order=factor_names,
                inject_behavior=True,
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
                    "concept_name": str(policy.get("concept", {}).get("name") or "concept"),
                    "behaviors": render_behaviors(policy),
                    "context": normalized_context or "- (no additional context provided)",
                    "factors_section": render_factors_section(factors_to_generate),
                },
            )
            response = await generate_structured(
                model,
                prompt,
                schema_name="policy_design",
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
                policy,
                factor_order=factor_names,
                inject_behavior=True,
            )

    if normalized_context:
        design["_context"] = normalized_context

    design_path = output_dir / DESIGN_FILE
    write_json(design_path, design)

    factor_sizes = {name: len(design[name]) for name in design_factors(design)}
    if factor_sizes:
        print(f"Design written to {design_path}")
        print(
            "Factor sizes: "
            + ", ".join(f"{name}={size}" for name, size in factor_sizes.items())
        )
    else:
        print(f"Design written to {design_path} (behavior only)")

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
            "policy_path": raw_cfg.get("policy_path") or str(suite_root / "policy.json"),
            "save_dir": raw_cfg.get("save_dir") or str(suite_root),
        },
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )

    result = await run_design(
        policy_path=cfg["policy_path"],
        out_dir=cfg["save_dir"],
        factors=factors,
        context=context,
        model=model_cfg.name if model_cfg is not None else None,
        level_count=level_count,
        reasoning_effort=model_cfg.reasoning_effort if model_cfg is not None else None,
        temperature=model_cfg.temperature if model_cfg is not None else None,
    )
    return {"design_path": result["design_path"]}
