"""Generate a taxonomy artifact from a behavior description."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

from p2m.config import parse_model_config, resolve_stage_paths
from p2m.core.config_model import (
    DEFAULT_SYSTEMATIZE_MAX_TOKENS,
    DEFAULT_SYSTEMATIZE_TEMPERATURE,
)
from p2m.core.io import write_json
from p2m.core.model_client import GenerateOptions, Message, generate_structured

BASE_DIR = Path(__file__).resolve().parents[2]
GEN_PROMPT = (BASE_DIR / "prompts" / "systematize_system.md").read_text()
DEFAULT_BEHAVIOR_CATEGORY_COUNT = 25
MIN_TAXONOMY_BEHAVIOR_CATEGORIES = 5

SCOPE = "suite"
SUITE_OUTPUT = "taxonomy.json"


def taxonomy_schema(*, min_behavior_categories: int = 0) -> Dict[str, Any]:
    """JSON schema for a risk taxonomy with behavior_categories and term definitions."""
    behavior_categories_prop: Dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "definition": {"type": "string"},
                "examples": {"type": "array", "items": {"type": "string"}},
                "permissible": {"type": "boolean"},
            },
            "required": ["name", "definition", "examples", "permissible"],
        },
    }
    if min_behavior_categories > 0:
        behavior_categories_prop["minItems"] = min_behavior_categories
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "behavior": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                },
                "required": ["name", "definition"],
            },
            "definition_of_terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "term": {"type": "string"},
                        "definition": {"type": "string"},
                        "examples": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["term", "definition", "examples"],
                },
            },
            "behavior_categories": behavior_categories_prop,
        },
        "required": ["behavior", "definition_of_terms", "behavior_categories"],
    }


TAXONOMY_SCHEMA = taxonomy_schema(min_behavior_categories=MIN_TAXONOMY_BEHAVIOR_CATEGORIES)


async def run_systematize(
    *,
    behavior: str,
    model: str,
    behavior_category_count: int = DEFAULT_BEHAVIOR_CATEGORY_COUNT,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    save_dir: str | None = None,
) -> dict[str, Any]:
    """Generate one taxonomy JSON artifact from the provided behavior text."""
    if not behavior:
        raise ValueError("behavior text is required")

    behavior_text = behavior.strip()
    temperature = temperature if temperature is not None else DEFAULT_SYSTEMATIZE_TEMPERATURE
    max_tokens = max_tokens if max_tokens is not None else DEFAULT_SYSTEMATIZE_MAX_TOKENS
    # Reasoning models don't support temperature
    if reasoning_effort is not None:
        temperature = None
    save_path = Path(save_dir) if save_dir else Path("artifacts/outputs")
    system_prompt = GEN_PROMPT.replace("{{BEHAVIOR_TARGET}}", str(behavior_category_count))
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=behavior_text),
    ]

    response = await generate_structured(
        model,
        messages,
        schema_name="taxonomy",
        json_schema=TAXONOMY_SCHEMA,
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        ),
    )
    taxonomy_json = response.parsed
    if not isinstance(taxonomy_json, dict):
        raise ValueError(
            f"taxonomy generation returned non-JSON output (model: {model}). "
            f"Raw text (first 500 chars): {(response.text or '')[:500]}"
        )

    save_path.mkdir(parents=True, exist_ok=True)
    taxonomy_path = save_path / "taxonomy.json"
    write_json(taxonomy_path, taxonomy_json)

    return {
        "taxonomy_path": str(taxonomy_path),
        "taxonomy": taxonomy_json,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and run taxonomy generation via systematization."""
    if "validators" in raw_cfg or "validator_models" in raw_cfg:
        raise ValueError("taxonomy validators are no longer supported")

    model = raw_cfg.get("model")
    if not isinstance(model, dict):
        raise ValueError("systematize.model must be a mapping")
    model_cfg = parse_model_config(
        model,
        field_name="systematize.model",
        default_temperature=DEFAULT_SYSTEMATIZE_TEMPERATURE,
        default_max_tokens=DEFAULT_SYSTEMATIZE_MAX_TOKENS,
    )

    behavior_category_count = raw_cfg.get("behavior_category_count", DEFAULT_BEHAVIOR_CATEGORY_COUNT)
    if not isinstance(behavior_category_count, int) or behavior_category_count < 1:
        raise ValueError("systematize.behavior_category_count must be a positive integer")

    web_search_raw = raw_cfg.get("web_search")
    if web_search_raw is not None and not isinstance(web_search_raw, bool):
        raise ValueError("systematize.web_search must be a boolean")
    web_search = web_search_raw if web_search_raw is not None else True

    suite_root = Path(ctx["suite_root"])
    save_dir = raw_cfg.get("save_dir") or ctx.get("systematize_artifact_dir") or str(suite_root)

    cfg = resolve_stage_paths(
        {"save_dir": save_dir},
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )

    behavior_name = ctx.get("behavior_name") or "behavior"
    behavior_description = ctx.get("behavior") or ""
    context = ctx.get("context")

    from p2m.core.async_utils import log_heartbeat
    from p2m.core.config_model import ModelConfig as SysModelConfig
    from p2m.stages.systematization import run_systematization
    from p2m.stages.systematization_convert import run_systematization_to_taxonomy

    sys_model_cfg = SysModelConfig(
        name=model_cfg.name,
        temperature=model_cfg.temperature,
        max_tokens=model_cfg.max_tokens or DEFAULT_SYSTEMATIZE_MAX_TOKENS,
        reasoning_effort=model_cfg.reasoning_effort,
    )
    sys_path = str(Path(cfg["save_dir"]) / "systematization.json")
    log.debug(f"systematize: model={model_cfg.name}, behavior_category_count={behavior_category_count}, web_search={web_search}")
    log.info("[systematize] [1/2] Researching behavior taxonomy...")
    async with log_heartbeat("[systematize] [1/2] Researching behavior taxonomy"):
        await run_systematization(
            behavior=behavior_name,
            behavior_text=behavior_description,
            save_path=sys_path,
            model_cfg=sys_model_cfg,
            web_search=web_search,
            context=context,
        )
    log.info("[systematize] [1/2] Behavior taxonomy complete")

    taxonomy_path_str = str(Path(cfg["save_dir"]) / "taxonomy.json")
    convert_model_cfg = SysModelConfig(
        name=model_cfg.name,
        temperature=model_cfg.temperature,
        max_tokens=model_cfg.max_tokens or DEFAULT_SYSTEMATIZE_MAX_TOKENS,
        reasoning_effort=model_cfg.reasoning_effort,
    )
    log.info("[systematize] [2/2] Converting to structured taxonomy...")
    async with log_heartbeat("[systematize] [2/2] Converting to structured taxonomy"):
        await run_systematization_to_taxonomy(
            systematization_path=sys_path,
            save_path=taxonomy_path_str,
            model_cfg=convert_model_cfg,
            behavior_category_count_hint=behavior_category_count,
        )

    return {
        "systematize_dir": cfg["save_dir"],
        "taxonomy_path": taxonomy_path_str,
    }
