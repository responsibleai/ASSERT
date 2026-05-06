"""Generate a policy artifact from a concept description."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

from p2m.config import parse_model_config, resolve_stage_paths
from p2m.core.config_model import (
    DEFAULT_POLICY_MAX_TOKENS,
    DEFAULT_POLICY_TEMPERATURE,
)
from p2m.core.io import write_json
from p2m.core.model_client import GenerateOptions, Message, generate_structured

BASE_DIR = Path(__file__).resolve().parents[2]
GEN_PROMPT = (BASE_DIR / "prompts" / "policy_system.md").read_text()
DEFAULT_BEHAVIOR_COUNT = 25
MIN_POLICY_BEHAVIORS = 5

SCOPE = "suite"
SUITE_OUTPUT = "policy.json"


def policy_schema(*, min_behaviors: int = 0) -> Dict[str, Any]:
    """JSON schema for a risk policy with behaviors and term definitions."""
    behaviors_prop: Dict[str, Any] = {
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
    if min_behaviors > 0:
        behaviors_prop["minItems"] = min_behaviors
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "concept": {
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
            "behaviors": behaviors_prop,
        },
        "required": ["concept", "definition_of_terms", "behaviors"],
    }


POLICY_SCHEMA = policy_schema(min_behaviors=MIN_POLICY_BEHAVIORS)


async def run_policy(
    *,
    concept: str,
    model: str,
    behavior_count: int = DEFAULT_BEHAVIOR_COUNT,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    save_dir: str | None = None,
) -> dict[str, Any]:
    """Generate one policy JSON artifact from the provided concept text."""
    if not concept:
        raise ValueError("concept text is required")

    concept_text = concept.strip()
    temperature = temperature if temperature is not None else DEFAULT_POLICY_TEMPERATURE
    max_tokens = max_tokens if max_tokens is not None else DEFAULT_POLICY_MAX_TOKENS
    # Reasoning models don't support temperature
    if reasoning_effort is not None:
        temperature = None
    save_path = Path(save_dir) if save_dir else Path("artifacts/outputs")
    system_prompt = GEN_PROMPT.replace("{{BEHAVIOR_TARGET}}", str(behavior_count))
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=concept_text),
    ]

    response = await generate_structured(
        model,
        messages,
        schema_name="policy",
        json_schema=POLICY_SCHEMA,
        options=GenerateOptions(
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        ),
    )
    policy_json = response.parsed
    if not isinstance(policy_json, dict):
        raise ValueError("policy generation returned non-JSON output")

    save_path.mkdir(parents=True, exist_ok=True)
    policy_path = save_path / "policy.json"
    write_json(policy_path, policy_json)

    return {
        "policy_path": str(policy_path),
        "policy": policy_json,
    }


async def run(ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate config and run policy generation via systematization."""
    if "validators" in raw_cfg or "validator_models" in raw_cfg:
        raise ValueError("policy validators are no longer supported")

    model = raw_cfg.get("model")
    if not isinstance(model, dict):
        raise ValueError("policy.model must be a mapping")
    model_cfg = parse_model_config(
        model,
        field_name="policy.model",
        default_temperature=DEFAULT_POLICY_TEMPERATURE,
        default_max_tokens=DEFAULT_POLICY_MAX_TOKENS,
    )

    behavior_count = raw_cfg.get("behavior_count", DEFAULT_BEHAVIOR_COUNT)
    if not isinstance(behavior_count, int) or behavior_count < 1:
        raise ValueError("policy.behavior_count must be a positive integer")

    web_search_raw = raw_cfg.get("web_search")
    if web_search_raw is not None and not isinstance(web_search_raw, bool):
        raise ValueError("policy.web_search must be a boolean")
    web_search = web_search_raw if web_search_raw is not None else True

    suite_root = Path(ctx["suite_root"])
    save_dir = raw_cfg.get("save_dir") or str(suite_root)

    cfg = resolve_stage_paths(
        {"save_dir": save_dir},
        cfg_path=ctx["config_path"],
        artifacts_root=ctx["artifacts_root"],
    )

    concept_name = ctx.get("concept_name") or "concept"
    concept_text = ctx.get("concept") or ""
    context = ctx.get("context")

    from p2m.core.async_utils import log_heartbeat
    from p2m.core.config_model import ModelConfig as SysModelConfig
    from p2m.stages.systematization import run_systematization
    from p2m.stages.systematization_convert import run_systematization_to_policy

    sys_model_cfg = SysModelConfig(
        name=model_cfg.name,
        temperature=model_cfg.temperature,
        max_tokens=model_cfg.max_tokens or DEFAULT_POLICY_MAX_TOKENS,
        reasoning_effort=model_cfg.reasoning_effort,
    )
    sys_path = str(Path(cfg["save_dir"]) / "systematization.json")
    log.debug(f"policy: model={model_cfg.name}, behavior_count={behavior_count}, web_search={web_search}")
    log.info("[1/2] Researching risk taxonomy...")
    async with log_heartbeat("[1/2] Researching risk taxonomy"):
        await run_systematization(
            concept=concept_name,
            concept_text=concept_text,
            save_path=sys_path,
            model_cfg=sys_model_cfg,
            web_search=web_search,
            context=context,
        )
    log.info("[1/2] Risk taxonomy complete")

    policy_path_str = str(Path(cfg["save_dir"]) / "policy.json")
    convert_model_cfg = SysModelConfig(
        name=model_cfg.name,
        temperature=model_cfg.temperature,
        max_tokens=model_cfg.max_tokens or DEFAULT_POLICY_MAX_TOKENS,
        reasoning_effort=model_cfg.reasoning_effort,
    )
    log.info("[2/2] Converting to structured policy...")
    async with log_heartbeat("[2/2] Converting to structured policy"):
        await run_systematization_to_policy(
            systematization_path=sys_path,
            save_path=policy_path_str,
            model_cfg=convert_model_cfg,
            behavior_count_hint=behavior_count,
        )

    return {
        "policy_dir": cfg["save_dir"],
        "policy_path": policy_path_str,
    }
